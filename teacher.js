/* Parallea teacher dashboard controller (vanilla, shared by 3 pages). */
(function(){
  const T = {};
  const POLL_MS = 4000;

  async function fetchJson(url, opts){
    const res = await fetch(url, Object.assign({credentials:'same-origin', headers:{'Content-Type':'application/json'}}, opts||{}));
    let body=null; try{ body = await res.json(); }catch(_){}
    return {ok:res.ok, status:res.status, body:body||{}};
  }

  function escapeHtml(s){ return String(s==null?'':s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  function toast(msg, kind){
    let t=document.querySelector('.toast'); if(!t){ t=document.createElement('div'); t.className='toast'; document.body.appendChild(t); }
    t.className='toast'; if(kind) t.classList.add(kind);
    t.textContent=msg; t.classList.add('show');
    clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('show'), 3200);
  }

  async function ensureAuthed(){
    const r = await fetchJson('/api/auth/me');
    if(!r.body || !r.body.user){ window.location.href='/auth/login'; return null; }
    if(r.body.user.role !== 'teacher' && r.body.user.role !== 'admin'){
      window.location.href='/student/personas';
      return null;
    }
    return r.body.user;
  }

  function statusTag(status){
    const t = document.createElement('span');
    t.className='tag';
    let dot='processing';
    if(status==='ready'){ t.classList.add('ok'); dot='ready'; }
    else if(status==='failed'){ t.classList.add('err'); dot='failed'; }
    else if(status==='uploaded'){ t.classList.add('muted'); dot='processing'; }
    else { t.classList.add('warn'); dot='processing'; }
    t.innerHTML = `<span class="status-dot ${dot}"></span>${escapeHtml(status||'?')}`;
    return t;
  }

  function navHTML(active, user){
    const items = [
      {href:'/teacher/dashboard', label:'Dashboard', key:'dashboard'},
      {href:'/teacher/upload', label:'Upload', key:'upload'},
      {href:'/teacher/roadmaps', label:'Roadmaps', key:'roadmaps'},
    ];
    const links = items.map(it=>`<a class="t-nav-link ${active===it.key?'active':''}" href="${it.href}">${it.label}</a>`).join('');
    const initials = (user.name||'').split(' ').map(s=>s[0]).filter(Boolean).slice(0,2).join('').toUpperCase()||'T';
    return `
      <header class="t-nav">
        <div class="t-shell t-nav-inner">
          <a class="t-brand" href="/teacher/dashboard" style="text-decoration:none;color:inherit">
            <div class="mark">P</div>
            <div><b>Parallea</b><span>Teacher Studio</span></div>
          </a>
          <nav class="t-nav-links">${links}</nav>
          <div class="t-user">
            <div class="avatar" title="${escapeHtml(user.email||'')}">${initials}</div>
            <div>${escapeHtml(user.name||'You')}<br><a href="/auth/logout">Sign out</a></div>
          </div>
        </div>
      </header>`;
  }

  function mountNav(active, user){
    const slot = document.getElementById('navMount');
    if(slot) slot.outerHTML = navHTML(active, user);
  }

  // ------------------------------------------------------------------ dashboard
  T.initDashboard = async function(){
    const user = await ensureAuthed(); if(!user) return;
    mountNav('dashboard', user);

    async function loadAll(){
      const [persona, videos, stats, avatars] = await Promise.all([
        fetchJson('/api/teacher/persona'),
        fetchJson('/api/teacher/videos'),
        fetchJson('/api/teacher/stats'),
        fetchJson('/api/teacher/avatars'),
      ]);
      renderPersonaOverview(persona.body.persona, stats.body.stats);
      renderVideoTable(videos.body.videos||[]);
      renderStats(stats.body.stats||{});
      renderAvatarPicker(persona.body.persona, avatars.body.presets||[]);
      renderPromptEditor(persona.body.persona);
      return videos.body.videos||[];
    }

    function renderPersonaOverview(persona, stats){
      const root = document.getElementById('personaOverview');
      if(!root) return;
      const preset = persona.avatar_preset || {};
      const promptStatus = persona.active_persona_prompt ? 'Active' : 'Not yet generated';
      const promptKlass = persona.active_persona_prompt ? 'tag ok' : 'tag warn';
      const initials = (persona.teacher_name||'T').split(' ').map(s=>s[0]).filter(Boolean).slice(0,2).join('').toUpperCase();
      root.innerHTML = `
        <div class="card-head">
          <h2>Persona overview</h2>
          <span class="${promptKlass}">${promptStatus}</span>
        </div>
        <div class="split">
          <div>
            <div style="display:flex;gap:14px;align-items:center;margin-bottom:14px">
              <div class="avatar-tile" style="margin:0;cursor:default;width:80px"><div class="blob" style="width:64px;height:64px;font-size:18px">${initials}</div></div>
              <div>
                <div style="font-size:20px;font-weight:600">${escapeHtml(persona.teacher_name||'')}</div>
                <div class="subtle">${escapeHtml(persona.profession||'add your profession in settings')}</div>
                <div class="subtle" style="margin-top:6px">Voice: <code>${escapeHtml(persona.voice_id||'default')}</code></div>
              </div>
            </div>
            <p class="subtle" style="margin:0">${escapeHtml(persona.style_summary || 'Upload your first video to generate a teaching style summary.')}</p>
          </div>
          <div>
            <div class="t-grid cols-2">
              <div class="stat"><span class="label">Videos</span><span class="value">${(stats||{}).videos_total||0}</span></div>
              <div class="stat"><span class="label">Topics</span><span class="value">${(persona.detected_topics||[]).length}</span></div>
              <div class="stat"><span class="label">Roadmap parts</span><span class="value">${(stats||{}).roadmap_parts_total||0}</span></div>
              <div class="stat"><span class="label">Sessions</span><span class="value">${(stats||{}).sessions_total||0}</span></div>
            </div>
          </div>
        </div>
        <div style="margin-top:14px;display:flex;flex-wrap:wrap;gap:6px">${(persona.detected_topics||[]).slice(0,12).map(t=>`<span class="tag muted">${escapeHtml(t)}</span>`).join(' ') || '<span class="subtle">Topics will appear after your first upload.</span>'}</div>
      `;
    }

    function renderStats(stats){
      const root = document.getElementById('statsCards'); if(!root) return;
      const mostAsked = (stats.most_asked_topics||[]).map(x=>`<li>${escapeHtml(x.topic)} <span class="subtle">· ${x.count}</span></li>`).join('') || '<li class="subtle">No student questions yet.</li>';
      root.innerHTML = `
        <div class="t-grid cols-4" style="margin-bottom:14px">
          <div class="stat"><span class="label">Uploaded</span><span class="value">${stats.videos_total||0}</span><span class="sub">${(stats.videos_by_status||{}).ready||0} ready</span></div>
          <div class="stat"><span class="label">Roadmaps</span><span class="value">${stats.roadmaps_total||0}</span><span class="sub">${stats.roadmap_parts_total||0} parts total</span></div>
          <div class="stat"><span class="label">Sessions</span><span class="value">${stats.sessions_total||0}</span><span class="sub">student conversations</span></div>
          <div class="stat"><span class="label">Failed</span><span class="value">${stats.failed_videos||0}</span><span class="sub">videos to retry</span></div>
        </div>
        <div class="card tight">
          <div class="card-head"><h3>Most asked topics</h3></div>
          <ul class="bullets">${mostAsked}</ul>
        </div>
      `;
    }

    function renderVideoTable(videos){
      const root = document.getElementById('videosCard'); if(!root) return;
      if(!videos.length){
        root.innerHTML = `<div class="card-head"><h2>Your videos</h2><a class="btn primary small" href="/teacher/upload">Upload your first video</a></div><div class="empty">No videos uploaded yet.</div>`;
        return;
      }
      const rows = videos.map(v=>{
        const thumb = v.thumbnail_url ? `<img class="thumb" src="${escapeHtml(v.thumbnail_url)}" alt="">` : `<div class="thumb"></div>`;
        const topics = (v.detected_topics||[]).slice(0,3).map(t=>`<span class="tag muted">${escapeHtml(t)}</span>`).join(' ');
        return `<div class="video-row" data-id="${escapeHtml(v.id)}">
          ${thumb}
          <div class="meta">
            <b>${escapeHtml(v.title||v.id)}</b>
            <div class="info">
              <span class="status-cell" data-status></span>
              <span>${v.parts_count||0} parts</span>
              ${topics}
              ${v.status_message?`<span class="subtle">· ${escapeHtml(v.status_message)}</span>`:''}
            </div>
          </div>
          <div class="acts">
            <a class="btn small" href="/teacher/videos/${escapeHtml(v.id)}">View</a>
            <button class="btn small" data-act="reprocess">Reprocess</button>
            <button class="btn small danger" data-act="delete">Delete</button>
          </div>
        </div>`;
      }).join('');
      root.innerHTML = `
        <div class="card-head"><h2>Your videos</h2><a class="btn primary small" href="/teacher/upload">Upload video</a></div>
        <div class="t-grid" style="gap:10px">${rows}</div>
      `;
      root.querySelectorAll('.video-row').forEach(row=>{
        const id = row.dataset.id;
        const v = videos.find(x=>x.id===id);
        const slot = row.querySelector('[data-status]');
        if(slot) slot.appendChild(statusTag(v.status));
        row.querySelector('[data-act="reprocess"]').addEventListener('click', async ()=>{
          await fetchJson(`/api/teacher/videos/${id}/reprocess`, {method:'POST'});
          toast('Reprocessing…','ok'); loadAll();
        });
        row.querySelector('[data-act="delete"]').addEventListener('click', async ()=>{
          if(!confirm('Delete this video and its roadmap?')) return;
          const r = await fetchJson(`/api/teacher/videos/${id}`, {method:'DELETE'});
          if(r.ok){ toast('Deleted.', 'ok'); loadAll(); } else { toast(r.body.detail||'Delete failed','error'); }
        });
      });
    }

    function renderAvatarPicker(persona, presets){
      const root = document.getElementById('avatarCard'); if(!root) return;
      const tiles = presets.map(p=>{
        const initials = (p.name||'').slice(0,2).toUpperCase();
        const sel = persona.avatar_preset_id===p.id;
        return `<button class="avatar-tile ${sel?'selected':''}" data-avatar="${escapeHtml(p.id)}" type="button">
          <div class="blob" style="background:linear-gradient(135deg,${p.style?.skin||'#dde8e5'},${p.style?.shirt||'#86b0a7'});color:#fff">${initials}</div>
          <b>${escapeHtml(p.name)}</b><small>${escapeHtml(p.voice||'default')}</small>
        </button>`;
      }).join('');
      root.innerHTML = `
        <div class="card-head"><h3>Avatar &amp; voice</h3><span class="subtle">Students see this on your persona card.</span></div>
        <div class="avatar-grid">${tiles}</div>
        <div class="field" style="margin-top:14px">
          <label>Voice ID (Edge TTS)</label>
          <input type="text" id="voiceInput" value="${escapeHtml(persona.voice_id||'')}" placeholder="en-US-JennyNeural">
        </div>
        <div style="display:flex;justify-content:flex-end"><button class="btn primary small" id="saveVoice">Save voice</button></div>
      `;
      root.querySelectorAll('[data-avatar]').forEach(btn=>{
        btn.addEventListener('click', async ()=>{
          const r = await fetchJson('/api/teacher/avatar', {method:'POST', body: JSON.stringify({avatar_preset_id: btn.dataset.avatar})});
          if(r.ok){ toast('Avatar updated.','ok'); loadAll(); } else { toast(r.body.detail||'Failed','error'); }
        });
      });
      const voiceInput = document.getElementById('voiceInput');
      document.getElementById('saveVoice').addEventListener('click', async ()=>{
        const r = await fetchJson('/api/teacher/voice', {method:'POST', body: JSON.stringify({voice_id: voiceInput.value.trim()})});
        if(r.ok){ toast('Voice saved.','ok'); } else { toast(r.body.detail||'Failed','error'); }
      });
    }

    function renderPromptEditor(persona){
      const root = document.getElementById('promptCard'); if(!root) return;
      root.innerHTML = `
        <div class="card-head"><h3>Persona prompt</h3>
          <span class="subtle">Manual edits create a new active version.</span>
        </div>
        <div class="prompt-meta">
          <span class="tag muted">${persona.active_persona_prompt ? 'Active' : 'Empty'}</span>
          <span>Updated ${escapeHtml(persona.updated_at||'never')}</span>
          <a href="#" id="versionsLink">View versions</a>
        </div>
        <div class="field"><label>Active persona prompt</label>
          <textarea id="promptInput" placeholder="Upload a video to auto-generate, or write your own.">${escapeHtml(persona.active_persona_prompt||'')}</textarea>
        </div>
        <div class="field-row">
          <div class="field"><label>Profession</label><input type="text" id="profInput" value="${escapeHtml(persona.profession||'')}"></div>
          <div class="field"><label>Style summary</label><input type="text" id="styleInput" value="${escapeHtml(persona.style_summary||'')}"></div>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px"><button class="btn ghost small" id="resetPrompt">Reset</button><button class="btn primary small" id="savePrompt">Save</button></div>
        <div id="versionsList" style="display:none;margin-top:14px"></div>
      `;
      const promptInput = document.getElementById('promptInput');
      const profInput = document.getElementById('profInput');
      const styleInput = document.getElementById('styleInput');
      const original = {prompt:persona.active_persona_prompt||'', profession:persona.profession||'', style:persona.style_summary||''};

      document.getElementById('resetPrompt').addEventListener('click', ()=>{
        promptInput.value = original.prompt;
        profInput.value = original.profession;
        styleInput.value = original.style;
      });
      document.getElementById('savePrompt').addEventListener('click', async ()=>{
        const body = {
          active_persona_prompt: promptInput.value,
          profession: profInput.value,
          style_summary: styleInput.value,
        };
        const r = await fetchJson('/api/teacher/persona', {method:'PATCH', body: JSON.stringify(body)});
        if(r.ok){ toast('Persona saved.','ok'); loadAll(); } else { toast(r.body.detail||'Save failed','error'); }
      });
      document.getElementById('versionsLink').addEventListener('click', async (ev)=>{
        ev.preventDefault();
        const list = document.getElementById('versionsList');
        if(list.style.display==='block'){ list.style.display='none'; return; }
        const r = await fetchJson('/api/teacher/persona/prompts');
        const versions = r.body.versions||[];
        if(!versions.length){ list.innerHTML = '<div class="empty">No versions yet.</div>'; list.style.display='block'; return; }
        list.innerHTML = versions.map(v=>`
          <div class="card tight" style="margin-bottom:10px">
            <div class="prompt-meta"><span class="tag ${v.is_active?'ok':'muted'}">v${v.version}${v.is_active?' · active':''}</span><span>${escapeHtml(v.reason||'')}</span><span class="subtle">${escapeHtml(v.created_at||'')}</span>${v.is_active?'':`<a href="#" data-activate="${escapeHtml(v.id)}">Activate</a>`}</div>
            <div class="persona-prompt">${escapeHtml((v.prompt||'').slice(0,800))}${(v.prompt||'').length>800?'…':''}</div>
          </div>
        `).join('');
        list.style.display='block';
        list.querySelectorAll('[data-activate]').forEach(a=>a.addEventListener('click', async (ev2)=>{
          ev2.preventDefault();
          const r2 = await fetchJson(`/api/teacher/persona/prompts/${a.dataset.activate}/activate`, {method:'POST'});
          if(r2.ok){ toast('Version activated.','ok'); loadAll(); } else { toast(r2.body.detail||'Failed','error'); }
        }));
      });
    }

    await loadAll();
    setInterval(loadAll, POLL_MS);
  };

  // ------------------------------------------------------------------ upload
  T.initUpload = async function(){
    const user = await ensureAuthed(); if(!user) return;
    mountNav('upload', user);

    const form = document.getElementById('uploadForm');
    const drop = document.getElementById('drop');
    const fileInput = document.getElementById('fileInput');
    const fileLabel = document.getElementById('fileLabel');
    const status = document.getElementById('uploadStatus');
    const progress = document.getElementById('progress');
    const progressBar = progress.querySelector('span');

    drop.addEventListener('click', ()=>fileInput.click());
    fileInput.addEventListener('change', ()=>{
      if(fileInput.files[0]) fileLabel.textContent = fileInput.files[0].name;
    });
    ['dragover','dragenter'].forEach(e=>drop.addEventListener(e, ev=>{ ev.preventDefault(); drop.classList.add('dragging'); }));
    ['dragleave','drop'].forEach(e=>drop.addEventListener(e, ev=>{ ev.preventDefault(); drop.classList.remove('dragging'); }));
    drop.addEventListener('drop', ev=>{
      if(ev.dataTransfer.files.length){
        fileInput.files = ev.dataTransfer.files;
        fileLabel.textContent = ev.dataTransfer.files[0].name;
      }
    });

    form.addEventListener('submit', async (ev)=>{
      ev.preventDefault();
      if(!fileInput.files[0]){ toast('Pick a video file first.','error'); return; }
      const fd = new FormData(form);
      progress.hidden = false;
      progressBar.style.width = '5%';
      status.innerHTML = '<span class="status-dot processing"></span> Uploading…';
      const xhr = new XMLHttpRequest();
      xhr.upload.onprogress = (e)=>{ if(e.lengthComputable){ progressBar.style.width = Math.min(80, (e.loaded/e.total)*70 + 5)+'%'; } };
      xhr.onload = ()=>{
        let body = {}; try{ body = JSON.parse(xhr.responseText); }catch(_){}
        if(xhr.status>=200 && xhr.status<300 && body.video){
          progressBar.style.width = '100%';
          const vid = body.video;
          status.innerHTML = `<span class="status-dot processing"></span> ${escapeHtml(vid.status_message||'queued')}`;
          pollUntilReady(vid.id);
        } else {
          status.innerHTML = `<span class="status-dot failed"></span> ${escapeHtml((body && body.detail) || 'Upload failed')}`;
          progressBar.style.width = '0%';
        }
      };
      xhr.onerror = ()=>{
        status.innerHTML = `<span class="status-dot failed"></span> Network error`;
      };
      xhr.open('POST','/api/teacher/videos/upload');
      xhr.send(fd);
    });

    async function pollUntilReady(videoId){
      const stages = {uploaded:'Queued', transcribing:'Transcribing audio', analyzing:'Analyzing transcript', generating:'Generating persona + roadmap', ready:'Ready', failed:'Failed'};
      const tick = async ()=>{
        const r = await fetchJson(`/api/teacher/videos/${videoId}`);
        if(!r.ok){ status.innerHTML = `<span class="status-dot failed"></span> Lost track of upload`; return; }
        const v = r.body.video;
        const label = stages[v.status]||v.status;
        if(v.status==='ready'){
          status.innerHTML = `<span class="status-dot ready"></span> Ready! <a href="/teacher/videos/${escapeHtml(v.id)}">View roadmap →</a>`;
          progressBar.style.width = '100%'; return;
        }
        if(v.status==='failed'){
          status.innerHTML = `<span class="status-dot failed"></span> ${escapeHtml(label)} — ${escapeHtml(v.status_message||'')}`; return;
        }
        status.innerHTML = `<span class="status-dot processing"></span> ${escapeHtml(label)}…`;
        setTimeout(tick, 3000);
      };
      tick();
    }
  };

  // ------------------------------------------------------------------ roadmaps
  T.initRoadmaps = async function(){
    const user = await ensureAuthed(); if(!user) return;
    mountNav('roadmaps', user);
    const root = document.getElementById('roadmapsMount');
    const r = await fetchJson('/api/teacher/roadmaps');
    const roadmaps = (r.body && r.body.roadmaps) || [];
    if(!roadmaps.length){
      root.innerHTML = `<div class="empty">No roadmaps yet. Upload a video to create the first one.</div>`;
      return;
    }
    root.innerHTML = roadmaps.map(rm => `
      <article class="card">
        <div class="card-head">
          <h2>${escapeHtml(rm.title || rm.video_title || 'Untitled roadmap')}</h2>
          <span class="tag muted">${escapeHtml(rm.difficulty || 'beginner')}</span>
        </div>
        <p class="subtle" style="margin-top:0">${escapeHtml(rm.video_title || '')}</p>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin:14px 0">
          ${(rm.topics||[]).map(t=>`<span class="tag">${escapeHtml(t)}</span>`).join('') || '<span class="tag muted">No topics</span>'}
        </div>
        <div class="t-grid cols-3">
          <div class="stat"><span class="label">Parts</span><span class="value">${rm.parts_count||0}</span></div>
          <div class="stat"><span class="label">Video</span><span class="value" style="font-size:18px">${escapeHtml(rm.video_status||'-')}</span></div>
          <div class="stat"><span class="label">Updated</span><span class="value" style="font-size:13px">${escapeHtml((rm.updated_at||'').slice(0,10) || '-')}</span></div>
        </div>
        <div style="display:flex;justify-content:flex-end;margin-top:16px">
          <a class="btn small" href="/teacher/videos/${escapeHtml(rm.video_id)}">View video</a>
        </div>
      </article>
    `).join('');
  };

  // ------------------------------------------------------------------ video detail
  T.initVideoDetail = async function(){
    const user = await ensureAuthed(); if(!user) return;
    mountNav(null, user);
    const videoId = document.body.dataset.videoId || (window.location.pathname.split('/').pop());
    const root = document.getElementById('videoMount');
    const r = await fetchJson(`/api/teacher/videos/${videoId}`);
    if(!r.ok){ root.innerHTML = `<div class="empty">Video not found.</div>`; return; }
    const v = r.body.video;
    const rm = r.body.roadmap;
    const partsHtml = (rm?.parts||[]).map(p=>`
      <div class="timeline-part">
        <b>${escapeHtml(p.title||p.part_id)}</b>
        <div class="range">${Math.round(p.start_time||0)}s – ${Math.round(p.end_time||0)}s</div>
        <div class="subtle" style="margin-top:6px">${escapeHtml(p.summary||'')}</div>
        <div class="info">
          ${(p.concepts||[]).map(c=>`<span class="tag muted">${escapeHtml(c)}</span>`).join(' ')}
          ${(p.equations||[]).map(eq=>`<span class="tag warn">${escapeHtml(eq)}</span>`).join(' ')}
          ${(p.examples||[]).map(ex=>`<span class="tag">${escapeHtml(ex)}</span>`).join(' ')}
        </div>
      </div>
    `).join('') || '<div class="empty">Roadmap not generated yet.</div>';

    root.innerHTML = `
      <div class="t-page-head">
        <div>
          <p class="kicker">Video</p>
          <h1>${escapeHtml(v.title||v.id)}</h1>
          <p>${escapeHtml(v.description || '')}</p>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn small" id="reprocessBtn">Reprocess</button>
          <button class="btn small danger" id="deleteBtn">Delete</button>
          <a class="btn small" href="/teacher/dashboard">Back to dashboard</a>
        </div>
      </div>
      <div class="split">
        <div>
          <div class="card">
            <div class="card-head"><h3>Roadmap parts</h3><span class="subtle">${(rm?.parts||[]).length} parts</span></div>
            <div class="timeline">${partsHtml}</div>
          </div>
        </div>
        <div>
          <div class="card">
            <div class="card-head"><h3>Status</h3><span id="statusSlot"></span></div>
            <div class="subtle">${escapeHtml(v.status_message||'')}</div>
            <hr>
            <div class="subtle"><b>Topics:</b> ${(v.detected_topics||[]).map(t=>`<span class="tag muted">${escapeHtml(t)}</span>`).join(' ')||'-'}</div>
            <div class="subtle" style="margin-top:8px"><b>Subject:</b> ${escapeHtml(v.subject||'-')}</div>
            <div class="subtle" style="margin-top:8px"><b>Created:</b> ${escapeHtml(v.created_at||'-')}</div>
            <div class="subtle" style="margin-top:8px"><b>Duration:</b> ${v.duration?Math.round(v.duration)+'s':'-'}</div>
          </div>
          <div class="card" style="margin-top:18px">
            <div class="card-head"><h3>Roadmap summary</h3></div>
            <p>${escapeHtml(rm?.summary || 'No summary yet.')}</p>
            <div class="subtle">Difficulty: ${escapeHtml(rm?.difficulty||'-')}</div>
          </div>
        </div>
      </div>
    `;
    document.getElementById('statusSlot').appendChild(statusTag(v.status));
    document.getElementById('reprocessBtn').addEventListener('click', async ()=>{
      const r2 = await fetchJson(`/api/teacher/videos/${videoId}/reprocess`, {method:'POST'});
      if(r2.ok){ toast('Reprocessing…','ok'); setTimeout(()=>location.reload(), 1500); } else toast(r2.body.detail||'Failed','error');
    });
    document.getElementById('deleteBtn').addEventListener('click', async ()=>{
      if(!confirm('Delete this video and its roadmap?')) return;
      const r2 = await fetchJson(`/api/teacher/videos/${videoId}`, {method:'DELETE'});
      if(r2.ok){ window.location.href='/teacher/dashboard'; } else toast(r2.body.detail||'Failed','error');
    });
  };

  window.ParalleaTeacher = T;
})();
