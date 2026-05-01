/* Parallea student controller (vanilla, shared by browse + learn pages). */
(function(){
  const S = {};

  async function fetchJson(url, opts){
    const res = await fetch(url, Object.assign({credentials:'same-origin', headers:{'Content-Type':'application/json'}}, opts||{}));
    let body=null; try{ body = await res.json(); }catch(_){}
    return {ok:res.ok, status:res.status, body:body||{}};
  }
  function escapeHtml(s){ return String(s==null?'':s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function initials(name){ return (name||'').split(' ').map(s=>s[0]).filter(Boolean).slice(0,2).join('').toUpperCase()||'T'; }

  async function ensureAuthed(role){
    const r = await fetchJson('/api/auth/me');
    if(!r.body || !r.body.user){ window.location.href='/auth/login'; return null; }
    if(role && r.body.user.role !== role && r.body.user.role !== 'admin'){
      window.location.href = r.body.user.role==='teacher' ? '/teacher/dashboard' : '/auth/login';
      return null;
    }
    return r.body.user;
  }

  function navHTML(user){
    return `
      <header class="s-nav">
        <div class="s-shell s-nav-inner">
          <a class="s-brand" href="/student/personas" style="text-decoration:none;color:inherit">
            <div class="mark">P</div>
            <div><b>Parallea</b><span>Learning</span></div>
          </a>
          <div class="s-user">
            <div class="avatar" title="${escapeHtml(user.email||'')}">${initials(user.name)}</div>
            <div>${escapeHtml(user.name||'You')}<br><a href="/auth/logout">Sign out</a></div>
          </div>
        </div>
      </header>`;
  }

  function mountNav(user){
    const slot = document.getElementById('navMount');
    if(slot) slot.outerHTML = navHTML(user);
  }

  // ------------------------------------------------------------------ browse
  S.initPersonaBrowse = async function(){
    const user = await ensureAuthed('student'); if(!user) return;
    mountNav(user);

    const grid = document.getElementById('personaGrid');
    const search = document.getElementById('search');

    const r = await fetchJson('/api/student/personas');
    if(!r.ok){
      grid.innerHTML = `<div class="empty" style="grid-column:1/-1">Could not load teachers right now.</div>`;
      return;
    }
    const personas = r.body.personas || [];

    function render(list){
      if(!list.length){
        grid.innerHTML = `<div class="empty" style="grid-column:1/-1">No teachers available yet.</div>`;
        return;
      }
      grid.innerHTML = list.map(p=>renderCard(p)).join('');
      grid.querySelectorAll('[data-pick]').forEach(btn=>btn.addEventListener('click', ()=>{
        window.location.href = `/student/learn/${btn.dataset.pick}`;
      }));
    }

    function renderCard(p){
      const ava = (p.avatar_preset && p.avatar_preset.style && p.avatar_preset.style.shirt) || '#86b0a7';
      const skin = (p.avatar_preset && p.avatar_preset.style && p.avatar_preset.style.skin) || '#f1c7a3';
      const topics = (p.detected_topics||[]).slice(0,3);
      const moreTopics = (p.detected_topics||[]).length - topics.length;
      const ready = !!p.ready;
      const pickBtn = ready
        ? `<button class="btn primary small" data-pick="${escapeHtml(p.id)}">Start learning →</button>`
        : `<button class="btn small" data-pick="${escapeHtml(p.id)}">Try anyway</button>`;
      return `
        <div class="persona-card">
          <div class="hover-spec">
            <b>${escapeHtml(p.teacher_name||'Teacher')}</b>
            <dl>
              <dt>Profession</dt><dd>${escapeHtml(p.profession||'-')}</dd>
              <dt>Style</dt><dd>${escapeHtml(p.style_summary||'A teacher in their own style.')}</dd>
              <dt>Teaches</dt><dd>${(p.detected_topics||[]).slice(0,5).join(', ')||'New persona, just getting started.'}</dd>
              <dt>Videos</dt><dd>${p.videos_count||0}</dd>
              <dt>Topics</dt><dd>${p.topics_count||0}</dd>
              <dt>Roadmap parts</dt><dd>${p.parts_total||0}</dd>
            </dl>
          </div>
          <div class="head">
            <div class="ava" style="background:linear-gradient(135deg,${skin},${ava})">${initials(p.teacher_name)}</div>
            <div>
              <div class="name">${escapeHtml(p.teacher_name||'Teacher')}</div>
              <div class="role">${escapeHtml(p.profession||'Teacher')}</div>
            </div>
          </div>
          <div class="style">${escapeHtml(p.style_summary || 'New on Parallea — pick to learn in their style.')}</div>
          <div class="topics">
            ${topics.map(t=>`<span class="tag">${escapeHtml(t)}</span>`).join(' ')}
            ${moreTopics>0?`<span class="tag muted">+${moreTopics}</span>`:''}
            ${!topics.length?`<span class="tag faint">No topics yet</span>`:''}
          </div>
          <div class="stats">
            <div><b>${p.videos_count||0}</b><span>videos</span></div>
            <div><b>${p.topics_count||0}</b><span>topics</span></div>
            <div><b>${p.parts_total||0}</b><span>parts</span></div>
          </div>
          <div class="actions">
            <span class="tag ${ready?'':'faint'}">${ready?'Ready to teach':'Setting up'}</span>
            ${pickBtn}
          </div>
        </div>`;
    }

    render(personas);

    if(search){
      search.addEventListener('input', ()=>{
        const q = (search.value||'').toLowerCase().trim();
        if(!q){ render(personas); return; }
        const filtered = personas.filter(p=>{
          const hay = [p.teacher_name, p.profession, p.style_summary, ...(p.detected_topics||[])].join(' ').toLowerCase();
          return hay.includes(q);
        });
        render(filtered);
      });
    }
  };

  // ---------------------------------------------------------------- learn (minimal placeholder; phase 6 will replace)
  S.initLearnSkeleton = async function(){
    const user = await ensureAuthed('student'); if(!user) return;
    mountNav(user);

    const personaId = document.body.dataset.personaId || window.location.pathname.split('/').pop();
    const sideMount = document.getElementById('sideMount');
    const convEl = document.getElementById('conversation');
    const input = document.getElementById('msgInput');
    const sendBtn = document.getElementById('sendBtn');

    const persona = (await fetchJson(`/api/student/personas/${personaId}`)).body.persona;
    if(!persona){ window.location.href='/student/personas'; return; }
    sideMount.innerHTML = `
      <div class="persona-banner">
        <div class="ava">${initials(persona.teacher_name)}</div>
        <div>
          <b>${escapeHtml(persona.teacher_name||'Teacher')}</b>
          <small>${escapeHtml(persona.profession||'')}</small>
        </div>
      </div>
      <div class="suggested" id="topicSuggest"></div>
      <div id="partCard"></div>
    `;
    const topicSlot = document.getElementById('topicSuggest');
    (persona.detected_topics||[]).slice(0,6).forEach(t=>{
      const b = document.createElement('button');
      b.textContent = t;
      b.addEventListener('click', ()=>{ input.value = `I want to learn ${t}.`; input.focus(); });
      topicSlot.appendChild(b);
    });

    let session = null;
    function pushMessage(role, content, extra){
      const div = document.createElement('div');
      div.className = `bubble ${role==='assistant'?'assistant':'student'}`;
      div.innerHTML = escapeHtml(content||'').replace(/\n/g,'<br>');
      if(extra && extra.disclaimer){
        const small = document.createElement('small');
        small.textContent = extra.disclaimer;
        div.appendChild(small);
      }
      convEl.appendChild(div);
      convEl.scrollTop = convEl.scrollHeight;
    }

    function renderPartCard(part, mode){
      const card = document.getElementById('partCard');
      if(!part){ card.innerHTML = mode==='persona_only' ? `<div class="part-card"><div class="head"><span>persona-only</span></div><b>Topic not in uploaded videos</b><div>${escapeHtml(persona.teacher_name)} is teaching in their own style.</div></div>` : ''; return; }
      card.innerHTML = `
        <div class="part-card">
          <div class="head"><span>video roadmap</span><span>${Math.round(part.start_time||0)}s – ${Math.round(part.end_time||0)}s</span></div>
          <b>${escapeHtml(part.title||'')}</b>
          <div>${escapeHtml(part.summary||'')}</div>
          <div class="suggested">
            ${(part.concepts||[]).slice(0,3).map(c=>`<button>${escapeHtml(c)}</button>`).join('')}
          </div>
        </div>`;
      card.querySelectorAll('button').forEach(b=>b.addEventListener('click', ()=>{
        input.value = `Tell me more about ${b.textContent}.`; input.focus();
      }));
    }

    function applyEnvelope(env){
      session = env.session;
      const m = env.message;
      if(m){
        const role = m.role==='assistant' ? 'assistant' : 'student';
        pushMessage(role, m.content, m.extra||{});
      }
      renderPartCard(env.currentPart, session.mode);
    }

    // open session
    const created = await fetchJson('/api/student/sessions', {method:'POST', body: JSON.stringify({persona_id: personaId})});
    if(!created.ok){ pushMessage('assistant', "Couldn't start a session."); return; }
    applyEnvelope(created.body);

    async function sendStudentText(text){
      pushMessage('student', text);
      input.value = '';
      const r = session.state==='awaiting_topic' || session.state==='greeting'
        ? await fetchJson(`/api/student/sessions/${session.id}/topic`, {method:'POST', body: JSON.stringify({topic: text})})
        : await fetchJson(`/api/student/sessions/${session.id}/message`, {method:'POST', body: JSON.stringify({content: text})});
      if(r.ok) applyEnvelope(r.body);
      else pushMessage('assistant', '(server error)');
    }
    sendBtn.addEventListener('click', ()=>{ const t = input.value.trim(); if(t) sendStudentText(t); });
    input.addEventListener('keydown', ev=>{ if(ev.key==='Enter' && !ev.shiftKey){ ev.preventDefault(); const t = input.value.trim(); if(t) sendStudentText(t); }});
  };

  window.ParalleaStudent = S;
})();
