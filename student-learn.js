/* Parallea immersive learning controller: avatar -> original video part -> Manim clarification. */
(function(){
  const I = {};

  const dom = {};
  let session = null;
  let persona = null;
  let user = null;
  let currentPart = null;
  let currentVideo = null;
  let currentRoadmapPartIds = [];
  let assistantSpeaking = false;
  let videoPlaying = false;
  let activeVideoToken = 0;
  let activeManimVideo = null;
  let activeManimMessageId = null;
  let visualCleanupRequested = false;
  const audioCleanupRequested = new Set();
  let manimSyncState = 'idle';
  let pendingTranscriptConfirmation = null;

  const VAD_CONFIG = {
    preSpeechPaddingMs: 450,
    postSpeechPaddingMs: 1000,
    minSpeechMs: 800,
    silenceDurationMs: 1250,
    maxUtteranceMs: 25000,
    recorderTimesliceMs: 250,
    minUploadBytes: 2048
  };
  const DEV_VOICE_DEBUG = ['localhost','127.0.0.1'].includes(location.hostname)
    || new URLSearchParams(location.search).has('voiceDebug');

  let vadStream = null;
  let vadAudioCtx = null;
  let vadAnalyser = null;
  let vadSource = null;
  let vadRecorder = null;
  let vadRecorderMimeType = '';
  let vadPreChunks = [];
  let vadSpeechChunks = [];
  let vadRecorderHeaderChunk = null;
  let vadFrame = 0;
  let vadEnabled = false;
  let vadSpeechActive = false;
  let vadSilenceSince = 0;
  let vadProcessing = false;
  let vadThreshold = 0.035;
  let vadDiscardTurn = false;
  let vadPendingFinalize = false;
  let vadFlushingFinalChunk = false;
  let vadFinalizeAt = 0;
  let vadUtteranceStartedAt = 0;
  let vadLastVoiceAt = 0;
  let vadLastBlob = null;
  let voiceDebugExpanded = false;
  let voiceDebug = {
    vadState: 'idle',
    durationMs: 0,
    blobSize: 0,
    chunkCount: 0,
    sttProvider: '',
    sttModel: '',
    rawTranscript: '',
    finalTranscript: '',
    sessionState: ''
  };

  async function fetchJson(url, opts){
    const options = Object.assign({credentials:'same-origin'}, opts || {});
    if(options.body && !(options.body instanceof FormData)){
      options.headers = Object.assign({'Content-Type':'application/json'}, options.headers || {});
    }
    const res = await fetch(url, options);
    let body = null;
    try{ body = await res.json(); }catch(_){}
    return {ok:res.ok, status:res.status, body:body || {}};
  }

  function $(id){ return document.getElementById(id); }
  function initials(name){ return (name || '').split(/\s+/).map(s => s[0]).filter(Boolean).slice(0,2).join('').toUpperCase() || 'T'; }
  function fmtTime(seconds){
    const n = Number(seconds || 0);
    const m = Math.floor(n / 60);
    const s = Math.floor(n % 60);
    return m ? `${m}:${String(s).padStart(2,'0')}` : `${s}s`;
  }
  function flowLog(message, data){
    try{ console.debug('[immersive-flow] ' + message, data || {}); }catch(_){}
  }

  async function notifyGeneratedMediaEnded(kind, messageId){
    if(!session || !messageId) return;
    const endpointKind = kind === 'visual' ? 'visual-ended' : 'audio-ended';
    const cleanupKey = `${endpointKind}:${messageId}`;
    if(kind === 'visual'){
      if(visualCleanupRequested) return;
      visualCleanupRequested = true;
    }else if(audioCleanupRequested.has(cleanupKey)){
      return;
    }else{
      audioCleanupRequested.add(cleanupKey);
    }
    flowLog(`${kind} ended; cleanup requested`, {messageId});
    try{
      const r = await fetchJson(`/api/student/sessions/${session.id}/messages/${messageId}/${endpointKind}`, {method:'POST'});
      flowLog(`${kind} cleanup ${r.ok ? 'success' : 'failure'}`, {messageId, status:r.status, body:r.body});
    }catch(err){
      flowLog(`${kind} cleanup failure`, {messageId, error:String(err)});
    }
  }
  function setManimSyncState(state, meta){
    manimSyncState = state || 'idle';
    flowLog('manim sync state', Object.assign({state: manimSyncState}, meta || {}));
  }
  function vadLog(message, data){
    try{ console.debug('[immersive-vad] ' + message, data || {}); }catch(_){}
  }

  function setVoiceDebug(patch){
    voiceDebug = Object.assign({}, voiceDebug, patch || {}, {sessionState: session && session.state || ''});
    const panel = document.getElementById('voiceDebugPanel');
    if(!panel) return;
    const fields = {
      dbgVadState: voiceDebug.vadState,
      dbgDuration: Math.round(voiceDebug.durationMs || 0) + ' ms',
      dbgBlobSize: (voiceDebug.blobSize || 0) + ' bytes',
      dbgChunks: voiceDebug.chunkCount || 0,
      dbgStt: [voiceDebug.sttProvider, voiceDebug.sttModel].filter(Boolean).join(' / ') || '-',
      dbgRawTranscript: voiceDebug.rawTranscript || '-',
      dbgFinalTranscript: voiceDebug.finalTranscript || '-',
      dbgSessionState: voiceDebug.sessionState || '-'
    };
    Object.keys(fields).forEach(id => {
      const el = document.getElementById(id);
      if(el) el.textContent = fields[id];
    });
  }

  function setVoiceDebugExpanded(expanded){
    voiceDebugExpanded = !!expanded;
    const wrap = document.getElementById('voiceDebugPanel');
    if(!wrap) return;
    wrap.classList.toggle('expanded', voiceDebugExpanded);
    wrap.classList.toggle('collapsed', !voiceDebugExpanded);
    flowLog('audio debug panel toggled', {expanded: voiceDebugExpanded});
  }

  function createVoiceDebugPanel(){
    if(!DEV_VOICE_DEBUG || document.getElementById('voiceDebugPanel')) return;
    const panel = document.createElement('aside');
    panel.id = 'voiceDebugPanel';
    panel.className = 'voice-debug collapsed';
    panel.innerHTML = `
      <button class="voice-debug-chip" id="voiceDebugToggle" type="button" aria-expanded="false">Audio Debug</button>
      <div class="voice-debug-body">
        <div class="voice-debug-head">
          <b>Voice Debug</b>
          <button class="voice-debug-minimize" id="voiceDebugMinimize" type="button" aria-label="Minimize audio debug">Minimize</button>
        </div>
        <div><span>VAD</span><code id="dbgVadState">idle</code></div>
        <div><span>Duration</span><code id="dbgDuration">0 ms</code></div>
        <div><span>Blob</span><code id="dbgBlobSize">0 bytes</code></div>
        <div><span>Chunks</span><code id="dbgChunks">0</code></div>
        <div><span>STT</span><code id="dbgStt">-</code></div>
        <div><span>Raw</span><code id="dbgRawTranscript">-</code></div>
        <div><span>Accepted</span><code id="dbgFinalTranscript">-</code></div>
        <div><span>Session</span><code id="dbgSessionState">-</code></div>
      </div>
    `;
    document.body.appendChild(panel);
    document.getElementById('voiceDebugToggle').addEventListener('click', () => {
      setVoiceDebugExpanded(true);
      document.getElementById('voiceDebugToggle').setAttribute('aria-expanded', 'true');
    });
    document.getElementById('voiceDebugMinimize').addEventListener('click', () => {
      setVoiceDebugExpanded(false);
      document.getElementById('voiceDebugToggle').setAttribute('aria-expanded', 'false');
    });
    setVoiceDebugExpanded(false);
    setVoiceDebug({});
  }

  function cacheDom(){
    [
      'appShell','avatarScene','videoScene','clarifyScene','hPersona','stateLabel',
      'avatarStage','avatarBlob','avatarImage','speakState','pName','pRole',
      'topicChips','voicePrompt','miniAvatarBlob','miniSpeakState','partTitle',
      'partTime','lessonVideo','videoStatus','clarifyAvatarBlob','clarifyTitle',
      'clarifySpeakState','manimStage','manimStatus','cueStrip','micBtn',
      'micLabel','vadMeter','flowHint','audioPlayer'
    ].forEach(id => { dom[id] = $(id); });
  }

  function setScene(scene){
    dom.appShell.dataset.scene = scene;
    dom.avatarScene.classList.toggle('hidden', scene !== 'avatar');
    dom.videoScene.classList.toggle('hidden', scene !== 'video');
    dom.clarifyScene.classList.toggle('hidden', scene !== 'clarify');
  }

  function setFlowHint(text){
    dom.flowHint.textContent = text || '';
  }

  function setSpeakState(state){
    const label = state || 'idle';
    dom.avatarStage.classList.remove('speaking','thinking','listening');
    if(label === 'speaking') dom.avatarStage.classList.add('speaking');
    if(label === 'thinking') dom.avatarStage.classList.add('thinking');
    if(label === 'listening') dom.avatarStage.classList.add('listening');
    dom.speakState.textContent = label;
    dom.clarifySpeakState.textContent = label;
    if(videoPlaying){
      dom.miniSpeakState.textContent = 'watching';
    }else if(label === 'speaking'){
      dom.miniSpeakState.textContent = 'speaking';
    }else{
      dom.miniSpeakState.textContent = label;
    }
  }

  function setSessionStatePill(){
    const state = session && session.state;
    const labels = {
      greeting:'greeting',
      awaiting_topic:'listening for topic',
      topic_matching:'matching topic',
      playing_video_part:'video part',
      awaiting_part_feedback:'listening for feedback',
      clarifying_part_doubt:'clarifying',
      awaiting_clarification_feedback:'checking clarity',
      persona_only_confirmation:'confirm fallback',
      persona_only_teaching:'persona-only',
      completed:'completed'
    };
    dom.stateLabel.textContent = labels[state] || state || 'connecting';
    setVoiceDebug({sessionState: state || ''});
  }

  function renderPersona(){
    if(!persona) return;
    const name = persona.teacher_name || 'Teacher';
    const role = persona.profession || 'Subject expert';
    dom.hPersona.textContent = name;
    dom.pName.textContent = name;
    dom.pRole.textContent = role;
    dom.avatarBlob.textContent = initials(name);
    dom.miniAvatarBlob.textContent = initials(name);
    dom.clarifyAvatarBlob.textContent = initials(name);
    const style = (persona.avatar_preset && persona.avatar_preset.style) || {};
    const bg = style.skin && style.shirt ? `linear-gradient(135deg,${style.skin},${style.shirt})` : '';
    if(bg){
      dom.avatarBlob.style.background = bg;
      dom.miniAvatarBlob.style.background = bg;
      dom.clarifyAvatarBlob.style.background = bg;
    }
    if(persona.avatar_image_url){
      dom.avatarImage.src = persona.avatar_image_url;
      dom.avatarStage.classList.add('has-image');
    }
    dom.topicChips.innerHTML = '';
    const topics = (persona.detected_topics || []).slice(0,10);
    topics.forEach(topic => {
      const chip = document.createElement('span');
      chip.textContent = topic;
      dom.topicChips.appendChild(chip);
    });
    flowLog('selected persona', {id: persona.id, teacher: name});
    flowLog('available topics announced', {topics});
  }

  function shouldListenForState(state){
    return [
      'awaiting_topic',
      'awaiting_part_feedback',
      'awaiting_clarification_feedback',
      'persona_only_confirmation',
      'persona_only_teaching',
      'completed'
    ].includes(state || '');
  }

  function updateMicButton(){
    dom.micBtn.classList.toggle('vad-active', !!vadEnabled);
    dom.micBtn.classList.toggle('recording', !!vadSpeechActive);
    dom.micLabel.textContent = vadEnabled ? 'Listening' : 'Enable mic';
    dom.micBtn.title = vadEnabled ? 'VAD listening is on' : 'Enable microphone';
  }

  function updateMeter(level){
    const bar = dom.vadMeter && dom.vadMeter.querySelector('span');
    if(!bar) return;
    const pct = Math.max(0, Math.min(100, Math.round((level || 0) / Math.max(vadThreshold * 2.2, 0.02) * 100)));
    bar.style.width = pct + '%';
  }

  function voiceLevel(){
    if(!vadAnalyser) return 0;
    const data = new Uint8Array(vadAnalyser.fftSize);
    vadAnalyser.getByteTimeDomainData(data);
    let sum = 0;
    for(const sample of data){
      const value = (sample - 128) / 128;
      sum += value * value;
    }
    return Math.sqrt(sum / data.length);
  }

  async function ensureVadMic(){
    if(vadStream && vadAnalyser){
      if(vadAudioCtx && vadAudioCtx.state === 'suspended') await vadAudioCtx.resume();
      return;
    }
    if(!navigator.mediaDevices) throw new Error('mediaDevices unavailable');
    vadStream = await navigator.mediaDevices.getUserMedia({audio:true});
    vadAudioCtx = vadAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if(vadAudioCtx.state === 'suspended') await vadAudioCtx.resume();
    vadSource = vadAudioCtx.createMediaStreamSource(vadStream);
    vadAnalyser = vadAudioCtx.createAnalyser();
    vadAnalyser.fftSize = 2048;
    vadAnalyser.smoothingTimeConstant = 0.82;
    vadSource.connect(vadAnalyser);
    vadLog('microphone ready', {
      audioContextSampleRate: vadAudioCtx.sampleRate,
      trackSampleRate: (vadStream.getAudioTracks()[0] && vadStream.getAudioTracks()[0].getSettings().sampleRate) || null
    });
  }

  async function calibrateVad(){
    const samples = [];
    const started = performance.now();
    while(performance.now() - started < 650){
      samples.push(voiceLevel());
      await new Promise(resolve => requestAnimationFrame(resolve));
    }
    const sorted = samples.slice().sort((a,b) => a - b);
    const median = sorted[Math.floor(sorted.length * 0.5)] || 0.008;
    const upper = sorted[Math.floor(sorted.length * 0.9)] || median;
    vadThreshold = Math.min(0.09, Math.max(median * 3.2, upper * 1.75, 0.018));
    vadLog('listening calibrated', {threshold: vadThreshold});
  }

  function pickMime(){
    const types = ['audio/webm;codecs=opus','audio/webm'];
    if(window.MediaRecorder){
      for(const type of types){
        if(MediaRecorder.isTypeSupported(type)) return type;
      }
    }
    return '';
  }

  function trimPreSpeechBuffer(now){
    const cutoff = now - VAD_CONFIG.preSpeechPaddingMs;
    vadPreChunks = vadPreChunks.filter(item => item.time >= cutoff);
  }

  function startContinuousRecorder(){
    if(!vadStream || (vadRecorder && vadRecorder.state !== 'inactive')) return;
    vadPreChunks = [];
    vadSpeechChunks = [];
    vadRecorderHeaderChunk = null;
    vadRecorderMimeType = pickMime();
    vadRecorder = vadRecorderMimeType ? new MediaRecorder(vadStream, {mimeType: vadRecorderMimeType}) : new MediaRecorder(vadStream);
    vadRecorderMimeType = vadRecorder.mimeType || vadRecorderMimeType || 'audio/webm';
    vadRecorder.ondataavailable = event => {
      if(!event.data || event.data.size <= 0) return;
      const item = {blob:event.data, time:performance.now()};
      if(!vadRecorderHeaderChunk) vadRecorderHeaderChunk = item;
      if(vadSpeechActive || vadPendingFinalize || vadFlushingFinalChunk){
        vadSpeechChunks.push(item);
      }else{
        vadPreChunks.push(item);
        trimPreSpeechBuffer(item.time);
      }
    };
    vadRecorder.onstop = () => {
      vadRecorder = null;
      vadPreChunks = [];
      vadSpeechChunks = [];
      vadRecorderHeaderChunk = null;
      vadFlushingFinalChunk = false;
    };
    vadRecorder.start(VAD_CONFIG.recorderTimesliceMs);
    vadLog('continuous recorder started', {mimeType: vadRecorderMimeType, timesliceMs: VAD_CONFIG.recorderTimesliceMs});
  }

  function stopContinuousRecorder(){
    vadDiscardTurn = true;
    if(vadRecorder && vadRecorder.state !== 'inactive'){
      try{ vadRecorder.stop(); }catch(_){}
    }
  }

  function startVadUtterance(level){
    vadSpeechActive = true;
    vadPendingFinalize = false;
    vadFlushingFinalChunk = false;
    vadFinalizeAt = 0;
    vadSilenceSince = 0;
    vadUtteranceStartedAt = performance.now();
    vadLastVoiceAt = vadUtteranceStartedAt;
    trimPreSpeechBuffer(vadUtteranceStartedAt);
    vadSpeechChunks = vadPreChunks.slice();
    vadPreChunks = [];
    setSpeakState('listening');
    updateMicButton();
    setVoiceDebug({vadState:'speaking', durationMs:0, blobSize:0, chunkCount:vadSpeechChunks.length});
    vadLog('speech detected', {
      level,
      state: session && session.state,
      preSpeechPaddingMs: VAD_CONFIG.preSpeechPaddingMs,
      preChunks: vadSpeechChunks.length
    });
  }

  async function finalizeVadUtterance(reason){
    if(vadProcessing) return;
    vadProcessing = true;
    vadSpeechActive = false;
    vadFlushingFinalChunk = true;
    vadFinalizeAt = 0;
    updateMicButton();
    setSpeakState('thinking');
    setFlowHint('Processing your voice...');
    if(vadRecorder && vadRecorder.state === 'recording'){
      await new Promise(resolve => {
        let done = false;
        const finish = () => {
          if(done) return;
          done = true;
          resolve();
        };
        try{
          vadRecorder.addEventListener('dataavailable', finish, {once:true});
          vadRecorder.requestData();
        }catch(_){
          finish();
        }
        setTimeout(finish, VAD_CONFIG.recorderTimesliceMs + 350);
      });
    }
    vadFlushingFinalChunk = false;
    vadPendingFinalize = false;
    const chunks = vadSpeechChunks.slice();
    vadSpeechChunks = [];
    const blobs = chunks.map(item => item.blob).filter(Boolean);
    if(vadRecorderHeaderChunk && vadRecorderMimeType.includes('webm') && blobs[0] !== vadRecorderHeaderChunk.blob){
      blobs.unshift(vadRecorderHeaderChunk.blob);
    }
    const durationMs = Math.max(0, (vadLastVoiceAt || performance.now()) - (vadUtteranceStartedAt || performance.now())) + VAD_CONFIG.preSpeechPaddingMs + VAD_CONFIG.postSpeechPaddingMs;
    const blob = blobs.length ? new Blob(blobs, {type: vadRecorderMimeType || 'audio/webm'}) : null;
    vadLastBlob = blob;
    setVoiceDebug({
      vadState:'processing',
      durationMs,
      blobSize: blob ? blob.size : 0,
      chunkCount: blobs.length
    });
    vadLog('speech ended', {
      reason,
      state: session && session.state,
      durationMs,
      blobSize: blob ? blob.size : 0,
      chunks: blobs.length,
      mimeType: vadRecorderMimeType,
      silenceDurationMs: VAD_CONFIG.silenceDurationMs,
      postSpeechPaddingMs: VAD_CONFIG.postSpeechPaddingMs
    });
    if(!blob || blob.size < VAD_CONFIG.minUploadBytes || durationMs < VAD_CONFIG.minSpeechMs){
      vadProcessing = false;
      vadPreChunks = [];
      setFlowHint('That audio was too short. Please say it again.');
      setSpeakState('listening');
      setVoiceDebug({vadState:'listening', blobSize: blob ? blob.size : 0, durationMs, chunkCount: blobs.length});
      vadLog('audio chunk rejected before upload', {blobSize: blob ? blob.size : 0, durationMs, chunks: blobs.length, mimeType: vadRecorderMimeType});
      return;
    }
    await transcribeVadBlob(blob, {durationMs, chunkCount:blobs.length, reason});
    vadProcessing = false;
    vadPreChunks = [];
    if(vadEnabled && shouldListenForState(session && session.state) && !assistantSpeaking && !videoPlaying){
      setSpeakState('listening');
      setVoiceDebug({vadState:'listening'});
      vadLog('next state', {state: session && session.state});
    }
  }

  function finishVadRecorder(skip){
    vadDiscardTurn = !!skip;
    if(skip){
      vadSpeechActive = false;
      vadPendingFinalize = false;
      vadFlushingFinalChunk = false;
      vadSpeechChunks = [];
    }else{
      finalizeVadUtterance('forced_finish');
    }
  }

  async function transcribeVadBlob(blob, meta){
    const fd = new FormData();
    const extension = (vadRecorderMimeType || '').includes('mp4') ? 'mp4' : ((vadRecorderMimeType || '').includes('ogg') ? 'ogg' : 'webm');
    fd.append('audio', blob, `student-turn.${extension}`);
    fd.append('client_duration_ms', String(Math.round(meta.durationMs || 0)));
    fd.append('client_mime_type', vadRecorderMimeType || blob.type || '');
    fd.append('client_sample_rate', String(vadAudioCtx ? vadAudioCtx.sampleRate : 0));
    fd.append('client_chunks', String(meta.chunkCount || 0));
    fd.append('client_session_state', session && session.state || '');
    vadLog('sending audio chunk to STT', {
      endpoint:'/transcribe-question',
      mimeType: vadRecorderMimeType || blob.type,
      blobSize: blob.size,
      durationMs: meta.durationMs,
      sampleRate: vadAudioCtx && vadAudioCtx.sampleRate,
      chunks: meta.chunkCount,
      reason: meta.reason
    });
    try{
      const res = await fetch('/transcribe-question', {method:'POST', credentials:'same-origin', body:fd});
      const data = await res.json().catch(() => ({}));
      const text = (data.question || data.transcript || '').trim();
      const stt = (data.metadata && data.metadata.stt) || {};
      const audio = (data.metadata && data.metadata.audio) || {};
      setVoiceDebug({
        sttProvider: stt.provider || '',
        sttModel: stt.model || '',
        rawTranscript: data.rawTranscript || text || '',
        blobSize: audio.size_bytes || blob.size,
        durationMs: audio.normalized_duration_sec ? audio.normalized_duration_sec * 1000 : meta.durationMs,
        chunkCount: audio.client_chunks || meta.chunkCount
      });
      vadLog('transcript received', {
        text,
        needsConfirmation: !!data.needsConfirmation,
        unclearReason: data.unclearReason,
        stt,
        nextState: session && session.state
      });
      if(!text){
        askRetry(data.message || 'Sorry, I did not catch that clearly. Could you say it again?');
        return;
      }
      const accepted = cleanTranscriptForState(text);
      if(pendingTranscriptConfirmation){
        await handleTranscriptOrConfirmation(text);
        return;
      }
      if(data.needsConfirmation){
        askTranscriptConfirmation(text, accepted, data.unclearReason);
        return;
      }
      await handleTranscriptOrConfirmation(accepted || text);
    }catch(err){
      vadLog('transcript failed', {error:String(err)});
      askRetry('Transcription failed. Please say it again.');
    }
  }

  function monitorVad(){
    if(!vadEnabled){
      vadFrame = 0;
      updateMeter(0);
      return;
    }
    if(assistantSpeaking || videoPlaying || vadProcessing || !shouldListenForState(session && session.state)){
      updateMeter(0);
      vadFrame = requestAnimationFrame(monitorVad);
      return;
    }
    const level = voiceLevel();
    updateMeter(level);
    const now = performance.now();
    if(level > vadThreshold){
      vadSilenceSince = 0;
      vadLastVoiceAt = now;
      if(vadPendingFinalize){
        vadPendingFinalize = false;
        vadFinalizeAt = 0;
        setFlowHint('Listening...');
      }
      if(!vadSpeechActive){
        startVadUtterance(level);
      }
      if(vadSpeechActive && now - vadUtteranceStartedAt >= VAD_CONFIG.maxUtteranceMs){
        finalizeVadUtterance('max_utterance');
      }
    }else if(vadSpeechActive){
      if(!vadSilenceSince) vadSilenceSince = performance.now();
      const silentFor = now - vadSilenceSince;
      if(!vadPendingFinalize && silentFor >= VAD_CONFIG.silenceDurationMs){
        vadPendingFinalize = true;
        vadFinalizeAt = now + VAD_CONFIG.postSpeechPaddingMs;
        setFlowHint('Pause detected. Holding briefly before transcription...');
        setVoiceDebug({vadState:'post-speech padding', durationMs: now - vadUtteranceStartedAt});
      }
      if(vadPendingFinalize && now >= vadFinalizeAt){
        finalizeVadUtterance('silence_timeout');
      }else if(now - vadUtteranceStartedAt >= VAD_CONFIG.maxUtteranceMs){
        finalizeVadUtterance('max_utterance');
      }else{
        setVoiceDebug({durationMs: Math.max(0, now - vadUtteranceStartedAt), chunkCount: vadSpeechChunks.length});
      }
    }
    vadFrame = requestAnimationFrame(monitorVad);
  }

  async function startVadListening(reason){
    if(vadEnabled){
      startContinuousRecorder();
      if(!vadFrame) monitorVad();
      setSpeakState('listening');
      setVoiceDebug({vadState:'listening'});
      return;
    }
    try{
      await ensureVadMic();
      await calibrateVad();
      vadEnabled = true;
      vadSpeechActive = false;
      vadSilenceSince = 0;
      vadProcessing = false;
    vadPendingFinalize = false;
    vadDiscardTurn = false;
    vadFlushingFinalChunk = false;
      startContinuousRecorder();
      updateMicButton();
      if(!vadFrame) monitorVad();
      setSpeakState('listening');
      setVoiceDebug({vadState:'listening'});
      setFlowHint('Listening...');
      vadLog('listening started', {reason: reason || 'manual', state: session && session.state, vadConfig: VAD_CONFIG});
    }catch(err){
      vadLog('listening failed', {error:String(err)});
      setFlowHint('Microphone permission is needed for voice turns.');
      updateMicButton();
    }
  }

  function stopVadListening(reason){
    vadEnabled = false;
    vadSpeechActive = false;
    vadSilenceSince = 0;
    vadProcessing = false;
    vadDiscardTurn = true;
    vadPendingFinalize = false;
    vadFlushingFinalChunk = false;
    if(vadFrame){
      cancelAnimationFrame(vadFrame);
      vadFrame = 0;
    }
    stopContinuousRecorder();
    updateMeter(0);
    updateMicButton();
    setVoiceDebug({vadState:'idle'});
    if(!assistantSpeaking && !videoPlaying) setSpeakState('idle');
    vadLog('listening stopped', {reason: reason || 'manual', state: session && session.state});
  }

  function pauseVadForAssistant(){
    assistantSpeaking = true;
    if(vadRecorder && vadRecorder.state !== 'inactive') stopContinuousRecorder();
    vadPreChunks = [];
    vadSpeechChunks = [];
    vadSpeechActive = false;
    vadPendingFinalize = false;
    setVoiceDebug({vadState:'assistant speaking'});
    vadLog('assistant speaking', {state: session && session.state});
  }

  function resumeVadAfterAssistant(){
    assistantSpeaking = false;
    vadLog('assistant finished', {nextState: session && session.state});
    if(videoPlaying){
      setSpeakState('idle');
      return;
    }
    if(shouldListenForState(session && session.state)){
      if(vadEnabled){
        startContinuousRecorder();
        setSpeakState('listening');
        if(!vadFrame) monitorVad();
        setVoiceDebug({vadState:'listening'});
        vadLog('next state', {state: session && session.state});
      }else{
        startVadListening('assistant_finished');
      }
    }else{
      setSpeakState('idle');
    }
  }

  function startManimSyncPlayback(){
    if(!activeManimVideo) return;
    try{ activeManimVideo.pause(); }catch(_){}
    try{ activeManimVideo.currentTime = 0; }catch(_){}
    try{ activeManimVideo.playbackRate = 1; }catch(_){}
    setManimSyncState('waiting_for_visual', {source:'prefetch'});
    flowLog('manim prepared, waiting for audio', {mode:'speech_and_manim'});
  }

  function alignManimToAudio(audio, video){
    if(!video) return;
    const videoDur = Number.isFinite(video.duration) ? video.duration : 0;
    const audioDur = Number.isFinite(audio.duration) ? audio.duration : 0;
    if(videoDur > 0.2 && audioDur > 0.2){
      const ratio = videoDur / audioDur;
      const clamped = Math.max(0.5, Math.min(2.0, ratio));
      try{ video.playbackRate = clamped; }catch(_){}
      flowLog('sync rate set', {videoDur, audioDur, ratio, applied: clamped});
    }
    try{ video.currentTime = 0; }catch(_){}
    try{
      const p = video.play();
      if(p && typeof p.catch === 'function') p.catch(() => {});
    }catch(_){}
  }

  async function playAssistantAudio(messageId){
    if(!messageId || !session) return;
    setSpeakState('speaking');
    setFlowHint('Teacher is speaking...');
    pauseVadForAssistant();
    startManimSyncPlayback();
    const r = await fetchJson(`/api/student/sessions/${session.id}/messages/${messageId}/audio`, {method:'POST'});
    if(!r.ok || !r.body.audio_url){
      setFlowHint('Audio was unavailable. Visual explanation is still showing.');
      setManimSyncState(activeManimVideo ? 'playing_speech_first' : 'visual_failed', {audio:false});
      resumeVadAfterAssistant();
      return;
    }
    const audio = dom.audioPlayer;
    const video = activeManimVideo;
    let done = false;
    const finish = () => {
      if(done) return;
      done = true;
      if(video){
        try{ video.pause(); }catch(_){}
      }
      resumeVadAfterAssistant();
    };
    audio.onended = () => {
      notifyGeneratedMediaEnded('audio', messageId);
      if(activeManimVideo){
        notifyGeneratedMediaEnded('visual', messageId);
        try{
          activeManimVideo.removeAttribute('src');
          activeManimVideo.load();
        }catch(_){}
      }
      finish();
      try{
        audio.removeAttribute('src');
        audio.load();
      }catch(_){}
    };
    audio.onerror = finish;
    const startBoth = () => {
      if(video) alignManimToAudio(audio, video);
      audio.play().catch(() => finish());
    };
    audio.onloadedmetadata = () => {
      if(!video){
        setManimSyncState('playing_speech_first', {audio:true, visual:false});
        startBoth();
        return;
      }
      const ready = Number.isFinite(video.duration) && video.duration > 0;
      if(ready){
        setManimSyncState('playing_synced', {audio:true, visual:true});
        startBoth();
      }else{
        const onMeta = () => {
          video.removeEventListener('loadedmetadata', onMeta);
          setManimSyncState('playing_synced', {audio:true, visual:true});
          startBoth();
        };
        video.addEventListener('loadedmetadata', onMeta);
        // Safety: if metadata never fires within 1.2s, start anyway.
        setTimeout(() => {
          if(done) return;
          if(!Number.isFinite(video.duration) || video.duration <= 0){
            video.removeEventListener('loadedmetadata', onMeta);
            setManimSyncState('playing_synced', {audio:true, visual:true, fallback:'no_video_metadata'});
            startBoth();
          }
        }, 1200);
      }
    };
    audio.src = r.body.audio_url;
    try{ audio.load(); }catch(_){}
  }

  function renderState(envelope){
    session = envelope.session || session;
    persona = Object.assign({}, persona || {}, envelope.persona || {});
    currentPart = envelope.currentPart || null;
    currentVideo = envelope.currentVideo || null;
    if(session && session.matched_part_ids && session.matched_part_ids.length){
      currentRoadmapPartIds = session.matched_part_ids;
    }
    setSessionStatePill();
    if(envelope.message && envelope.message.role === 'assistant'){
      dom.voicePrompt.textContent = envelope.message.content || '';
    }else if(session && session.state === 'playing_video_part'){
      dom.voicePrompt.textContent = 'Watch this part from the original teacher video.';
    }
  }

  function applyEnvelope(envelope, opts){
    opts = opts || {};
    if(!envelope || !envelope.session) return;
    renderState(envelope);
    const message = envelope.message || null;
    const extra = (message && message.extra) || {};
    const routing = extra.routing || {};
    if(routing.matchedRoadmapId || session.current_roadmap_id){
      flowLog('matched roadmap', {
        matchedRoadmapId: routing.matchedRoadmapId || session.current_roadmap_id,
        confidence: routing.confidence || session.confidence
      });
    }
    if(currentPart){
      flowLog('matched roadmap part', {partId: currentPart.id, index: session.current_part_index});
    }

    if(session.state === 'playing_video_part' || envelope.promptFor === 'video_part'){
      playOriginalVideoPart(envelope);
      return;
    }

    const visual = envelope.visual || extra.visual || null;
    if(visual && visual.type === 'manim'){
      renderClarification(envelope, visual);
      if(message && message.role === 'assistant' && opts.autoplay !== false){
        playAssistantAudio(message.id);
      }
      return;
    }

    activeManimVideo = null;
    activeManimMessageId = null;
    setManimSyncState('idle');
    setScene('avatar');
    if(message && message.role === 'assistant' && opts.autoplay !== false){
      playAssistantAudio(message.id);
    }else if(shouldListenForState(session.state) && !assistantSpeaking && !videoPlaying){
      startVadListening('state_ready');
    }
  }

  function resetVideoHandlers(){
    const video = dom.lessonVideo;
    video.onloadedmetadata = null;
    video.ontimeupdate = null;
    video.onended = null;
    video.onerror = null;
  }

  function playOriginalVideoPart(envelope){
    const part = envelope.currentPart;
    const videoInfo = envelope.currentVideo;
    setScene('video');
    activeManimVideo = null;
    activeManimMessageId = null;
    setManimSyncState('idle', {source:'original_video_part'});
    videoPlaying = true;
    setSpeakState('idle');
    if(vadRecorder && vadRecorder.state !== 'inactive') stopContinuousRecorder();
    setVoiceDebug({vadState:'video playback'});
    const token = ++activeVideoToken;
    const video = dom.lessonVideo;
    resetVideoHandlers();
    video.pause();
    video.removeAttribute('src');
    video.load();

    if(!part || !videoInfo || !videoInfo.stream_url){
      videoPlaying = false;
      dom.videoStatus.textContent = 'The original video file is missing for this part.';
      setFlowHint('The video part could not be loaded.');
      return;
    }

    const start = Math.max(0, Number(part.start_time || 0));
    const rawEnd = Number(part.end_time || 0);
    let end = rawEnd > start ? rawEnd : null;
    dom.partTitle.textContent = part.title || 'Lesson part';
    dom.partTime.textContent = end ? `${fmtTime(start)} - ${fmtTime(end)}` : `${fmtTime(start)}+`;
    dom.videoStatus.textContent = 'Preparing original teacher video part...';
    setFlowHint('Playing the original uploaded teacher video part.');
    flowLog('original video segment start', {videoId: videoInfo.id, partId: part.id, start, end});

    const finishPart = async reason => {
      if(token !== activeVideoToken || !videoPlaying) return;
      videoPlaying = false;
      resetVideoHandlers();
      try{ video.pause(); }catch(_){}
      dom.videoStatus.textContent = 'Video part finished.';
      flowLog('original video segment end', {partId: part.id, reason});
      setFlowHint('Checking understanding...');
      const r = await fetchJson(`/api/student/sessions/${session.id}/part-ended`, {method:'POST'});
      if(r.ok) applyEnvelope(r.body);
      else setFlowHint('Could not advance after the video part.');
    };

    video.onloadedmetadata = async () => {
      if(token !== activeVideoToken) return;
      if(!end && Number.isFinite(video.duration) && video.duration > start) end = video.duration;
      try{ video.currentTime = start; }catch(_){}
      dom.videoStatus.textContent = 'Playing original teacher video segment.';
      flowLog('video segment playback start', {
        partId: part.id,
        start,
        end,
        naturalWidth: video.videoWidth,
        naturalHeight: video.videoHeight,
        containerWidth: video.clientWidth,
        containerHeight: video.clientHeight
      });
      try{
        await video.play();
      }catch(_){
        dom.videoStatus.textContent = 'Use the video controls to start this part.';
      }
    };
    video.ontimeupdate = () => {
      if(token !== activeVideoToken || !videoPlaying) return;
      if(end && video.currentTime >= end - 0.12){
        finishPart('segment_end_time');
      }
    };
    video.onended = () => finishPart('video_ended');
    video.onerror = () => {
      videoPlaying = false;
      dom.videoStatus.textContent = 'The video failed to load.';
      setFlowHint('The video failed, but the session is still active.');
    };
    video.src = videoInfo.stream_url;
    video.load();
  }

  function buildManimUrl(rawUrl){
    if(!rawUrl) return '';
    if(/^https?:\/\//i.test(rawUrl) && /(?:X-Amz-Signature|X-Amz-Credential|Signature=)/i.test(rawUrl)){
      return rawUrl;
    }
    const sep = rawUrl.indexOf('?') >= 0 ? '&' : '?';
    return `${rawUrl}${sep}t=${Date.now()}`;
  }

  function renderClarification(envelope, visual){
    setScene('clarify');
    videoPlaying = false;
    const part = envelope.currentPart || currentPart;
    const message = envelope.message || {};
    const extra = message.extra || {};
    dom.clarifyTitle.textContent = (part && part.title) || 'Visual explanation';
    dom.manimStage.innerHTML = '';
    activeManimVideo = null;
    activeManimMessageId = message.id || null;
    visualCleanupRequested = false;

    const status = visual.status || visual.renderStatus || 'pending';
    if(visual.manim_duration_seconds || visual.estimated_spoken_duration_seconds){
      flowLog('manim duration metadata', {
        manimDuration: visual.manim_duration_seconds,
        estimatedSpokenDuration: visual.estimated_spoken_duration_seconds,
        ratio: visual.visual_to_audio_duration_ratio
      });
    }
    setManimSyncState('waiting_for_visual', {status});
    const rawUrl = visual.videoUrl
      || visual.media_url
      || (visual.payload && (visual.payload.video_url || visual.payload.media_url))
      || '';
    const timestamps = visual.timestamps || (visual.visual && visual.visual.timestamps) || [];
    const segments = (visual.syncPlan && visual.syncPlan.segments) || (extra.syncPlan && extra.syncPlan.segments) || [];
    renderCues(timestamps, segments);
    flowLog('sync plan used', {status, cueCount: timestamps.length || segments.length, usedFallback: !!visual.usedFallback});

    if(status === 'ready' && rawUrl){
      const cacheBustUrl = buildManimUrl(rawUrl);
      const v = document.createElement('video');
      v.id = 'clarificationManim';
      v.className = 'manim-video';
      v.src = cacheBustUrl;
      v.muted = true;
      v.autoplay = true;
      v.loop = false;
      v.playsInline = true;
      v.controls = false;
      v.preload = 'auto';
      v.setAttribute('playsinline', '');
      v.setAttribute('muted', '');
      v.setAttribute('autoplay', '');
      v.onloadedmetadata = () => {
        flowLog('frontend received_manim_url', {url: cacheBustUrl});
        flowLog('manim visual loaded', {
          naturalWidth: v.videoWidth,
          naturalHeight: v.videoHeight,
          containerWidth: v.clientWidth,
          containerHeight: v.clientHeight
        });
      };
      v.oncanplay = () => flowLog('frontend manim_video_loaded', {url: cacheBustUrl});
      v.onended = () => {
        notifyGeneratedMediaEnded('visual', activeManimMessageId);
        try{
          v.removeAttribute('src');
          v.load();
        }catch(_){}
      };
      v.onerror = () => {
        flowLog('frontend manim_video_error', {url: cacheBustUrl, code: v.error && v.error.code});
        setManimSyncState('visual_failed', {url: cacheBustUrl});
        const err = document.createElement('div');
        err.className = 'manim-status error';
        err.textContent = 'Visual failed to render, but audio explanation is available.';
        dom.manimStage.innerHTML = '';
        dom.manimStage.appendChild(err);
        activeManimVideo = null;
      };
      dom.manimStage.appendChild(v);
      activeManimVideo = v;
      setManimSyncState('waiting_for_visual', {url: cacheBustUrl, ready:true});
      // Best-effort autoplay — muted so the browser allows it. Speech audio is separate.
      try{
        const playPromise = v.play();
        if(playPromise && typeof playPromise.catch === 'function'){
          playPromise.catch(err => flowLog('manim autoplay deferred', {error: String(err)}));
        }
      }catch(_){}
      setFlowHint(visual.usedFallback ? 'Playing fallback Manim visual.' : 'Playing the teacher clarification with Manim.');
      return;
    }

    if(status === 'not_needed'){
      const note = document.createElement('div');
      note.className = 'manim-status';
      note.textContent = 'No visual needed for this clarification.';
      dom.manimStage.appendChild(note);
      setManimSyncState('idle', {status:'not_needed'});
      return;
    }

    const statusNode = document.createElement('div');
    const isFailed = status === 'failed';
    statusNode.className = 'manim-status' + (isFailed ? ' error' : '');
    statusNode.textContent = isFailed
      ? (visual.error || 'Visual failed to render, but audio explanation is available.')
      : 'Preparing visual explanation...';
    dom.manimStage.appendChild(statusNode);
    setManimSyncState(isFailed ? 'visual_failed' : 'waiting_for_visual', {status});
  }

  function likelyClarificationRequest(text){
    const norm = (text || '').toLowerCase().replace(/[^a-z0-9' ]+/g,' ').replace(/\s+/g,' ').trim();
    if(!norm) return false;
    if(session && session.state === 'awaiting_part_feedback'){
      if(['no','nope','nothing','all good','continue','next'].includes(norm)) return false;
      if(norm.includes('got it') || norm.includes('understand') || norm.includes('clear') || norm.includes('move on')) return false;
      return true;
    }
    if(session && session.state === 'awaiting_clarification_feedback'){
      if(['yes','yeah','yep','ok','okay','continue','next'].includes(norm)) return false;
      if(norm.includes('makes sense') || norm.includes('got it') || norm.includes('understand') || norm.includes('clear')) return false;
      return true;
    }
    return false;
  }

  function showClarificationLoading(){
    setScene('clarify');
    activeManimVideo = null;
    activeManimMessageId = null;
    setManimSyncState('waiting_for_visual', {status:'rendering'});
    dom.clarifyTitle.textContent = (currentPart && currentPart.title) || 'Visual explanation';
    dom.manimStage.innerHTML = '';
    const status = document.createElement('div');
    status.className = 'manim-status';
    status.textContent = 'Rendering the visual explanation...';
    dom.manimStage.appendChild(status);
    dom.cueStrip.innerHTML = '';
    setFlowHint('Generating speech and Manim clarification...');
  }

  function renderCues(timestamps, segments){
    dom.cueStrip.innerHTML = '';
    const cueItems = [];
    (timestamps || []).forEach(item => {
      if(item && item.cue) cueItems.push(`${fmtTime(item.start)} ${item.cue}`);
    });
    if(!cueItems.length){
      (segments || []).forEach(item => {
        const cue = item.visualCue || item.cue || item.speechText || '';
        if(cue) cueItems.push(`${fmtTime(item.startHint || 0)} ${cue}`);
      });
    }
    cueItems.slice(0,8).forEach(text => {
      const span = document.createElement('span');
      span.textContent = text;
      dom.cueStrip.appendChild(span);
    });
  }

  function normalizeTranscriptText(text){
    return (text || '').replace(/\s+/g,' ').trim();
  }

  function cleanTopicTranscript(text){
    const cleaned = normalizeTranscriptText(text).replace(/[.?!,;:]+$/g,'').trim();
    const patterns = [
      /^(?:i\s+)?(?:want|wanna|would like|need)\s+to\s+(?:learn|study|understand|know)\s+(?:about\s+)?(.+)$/i,
      /^(?:can|could)\s+you\s+(?:teach|explain|show)\s+(?:me\s+)?(?:about\s+)?(.+)$/i,
      /^(?:teach|explain|show)\s+(?:me\s+)?(?:about\s+)?(.+)$/i,
      /^(?:let'?s|lets)\s+(?:learn|study)\s+(?:about\s+)?(.+)$/i,
      /^(?:i'?m|i am)\s+interested\s+in\s+(.+)$/i
    ];
    for(const pattern of patterns){
      const match = cleaned.match(pattern);
      if(match && match[1]){
        return match[1].replace(/\b(today|please|sir|ma'?am|mam)$/i,'').trim() || cleaned;
      }
    }
    return cleaned;
  }

  function cleanTranscriptForState(text){
    const cleaned = normalizeTranscriptText(text);
    if(session && (session.state === 'awaiting_topic' || session.state === 'greeting')){
      return cleanTopicTranscript(cleaned);
    }
    return cleaned;
  }

  function isAffirmative(text){
    const norm = normalizeTranscriptText(text).toLowerCase().replace(/[^a-z ]+/g,'').trim();
    return ['yes','yeah','yep','ok','okay','correct','right','thats right','that is right'].includes(norm)
      || norm.includes('got it right')
      || norm.includes('yes it is');
  }

  function isNegative(text){
    const norm = normalizeTranscriptText(text).toLowerCase().replace(/[^a-z ]+/g,'').trim();
    return ['no','nope','wrong','incorrect','not right'].includes(norm)
      || norm.includes('did not')
      || norm.includes("didnt")
      || norm.includes('say again');
  }

  function speakLocalPrompt(text){
    assistantSpeaking = true;
    if(vadRecorder && vadRecorder.state !== 'inactive') stopContinuousRecorder();
    setSpeakState('speaking');
    setVoiceDebug({vadState:'assistant speaking'});
    const done = () => {
      assistantSpeaking = false;
      if(vadEnabled && shouldListenForState(session && session.state) && !videoPlaying){
        startContinuousRecorder();
        setSpeakState('listening');
        setVoiceDebug({vadState:'listening'});
        if(!vadFrame) monitorVad();
      }else{
        setSpeakState('idle');
      }
    };
    if(!('speechSynthesis' in window)){
      setTimeout(done, 900);
      return;
    }
    try{
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 0.96;
      utterance.lang = 'en-IN';
      utterance.onend = done;
      utterance.onerror = done;
      window.speechSynthesis.speak(utterance);
    }catch(_){
      done();
    }
  }

  function askRetry(message){
    pendingTranscriptConfirmation = null;
    dom.voicePrompt.textContent = message;
    setFlowHint(message);
    speakLocalPrompt(message);
    setVoiceDebug({vadState:'listening'});
  }

  function askTranscriptConfirmation(rawTranscript, acceptedTranscript, reason){
    pendingTranscriptConfirmation = {rawTranscript, acceptedTranscript, reason};
    const message = rawTranscript
      ? `I heard: "${rawTranscript}". Did I get that right?`
      : 'Sorry, I did not catch that clearly. Could you say it again?';
    dom.voicePrompt.textContent = message;
    setFlowHint('Waiting for transcript confirmation...');
    setVoiceDebug({rawTranscript, finalTranscript:'', vadState:'confirming transcript'});
    speakLocalPrompt(message);
  }

  async function acceptTranscript(accepted, raw){
    const finalText = normalizeTranscriptText(accepted || raw);
    if(!finalText){
      askRetry('Sorry, I did not catch that clearly. Could you say it again?');
      return;
    }
    pendingTranscriptConfirmation = null;
    setVoiceDebug({finalTranscript: finalText});
    await sendStudent(finalText, {fromVad:true, rawTranscript: raw});
  }

  async function handleTranscriptOrConfirmation(text){
    if(pendingTranscriptConfirmation){
      if(isAffirmative(text)){
        const accepted = pendingTranscriptConfirmation.acceptedTranscript || pendingTranscriptConfirmation.rawTranscript;
        await acceptTranscript(accepted, pendingTranscriptConfirmation.rawTranscript);
        return;
      }
      if(isNegative(text)){
        askRetry('Sorry, could you say it again?');
        return;
      }
      const accepted = cleanTranscriptForState(text);
      pendingTranscriptConfirmation = null;
      await acceptTranscript(accepted, text);
      return;
    }
    await acceptTranscript(cleanTranscriptForState(text), text);
  }

  async function sendStudent(text, opts){
    opts = opts || {};
    const clean = (text || '').trim();
    if(!clean || !session) return;
    if(session.state === 'awaiting_topic' || session.state === 'greeting'){
      flowLog('student topic transcript', {text: clean});
    }else if(session.state === 'awaiting_part_feedback' || session.state === 'awaiting_clarification_feedback'){
      flowLog('part feedback transcript', {state: session.state, text: clean});
    }
    setSpeakState('thinking');
    setFlowHint('Thinking...');
    if(vadRecorder && vadRecorder.state !== 'inactive') stopContinuousRecorder();
    setVoiceDebug({vadState:'routing transcript', finalTranscript: clean});
    vadProcessing = true;
    const isTopicTurn = session.state === 'awaiting_topic' || session.state === 'greeting';
    const url = isTopicTurn
      ? `/api/student/sessions/${session.id}/topic`
      : `/api/student/sessions/${session.id}/message`;
    const body = isTopicTurn ? {topic:clean} : {content:clean};
    if(!isTopicTurn && likelyClarificationRequest(clean)){
      flowLog('clarification requested', {state: session.state, text: clean});
      showClarificationLoading();
    }
    const r = await fetchJson(url, {method:'POST', body:JSON.stringify(body)});
    vadProcessing = false;
    if(!r.ok){
      setSpeakState('idle');
      setFlowHint((r.body && r.body.detail) || 'Server error.');
      if(vadEnabled && shouldListenForState(session.state)) resumeVadAfterAssistant();
      return;
    }
    applyEnvelope(r.body);
  }

  async function ensureAuthed(){
    const r = await fetchJson('/api/auth/me');
    if(!r.body || !r.body.user){
      window.location.href = '/auth/login';
      return null;
    }
    if(r.body.user.role !== 'student' && r.body.user.role !== 'admin'){
      window.location.href = '/teacher/dashboard';
      return null;
    }
    return r.body.user;
  }

  I.init = async function(){
    cacheDom();
    createVoiceDebugPanel();
    user = await ensureAuthed();
    if(!user) return;
    const personaId = window.location.pathname.split('/').pop();
    const detail = await fetchJson(`/api/student/personas/${personaId}`);
    if(!detail.ok){
      window.location.href = '/student/personas';
      return;
    }
    persona = detail.body.persona;
    renderPersona();
    setSpeakState('idle');
    setFlowHint('Starting session...');

    const created = await fetchJson('/api/student/sessions', {method:'POST', body:JSON.stringify({persona_id: personaId})});
    if(!created.ok){
      setFlowHint('Could not start the session. Refresh and try again.');
      return;
    }
    applyEnvelope(created.body);

    dom.micBtn.addEventListener('click', () => {
      if(vadEnabled) stopVadListening('manual_toggle');
      else startVadListening('manual_toggle');
    });
  };

  window.ParalleaImmersive = I;
})();
