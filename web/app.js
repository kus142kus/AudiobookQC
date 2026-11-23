'use strict';
const $ = (id) => document.getElementById(id);
const state = {id:null, view:'new', audio:'', text:'', all:false, project:null, projects:[], selected:null, page:0, clip:null, busy:false};
const labels = {replacement:'Замена', omission:'Пропуск', repeat:'Повтор', extra:'Вне текста', uncertain:'Сомнительно', clipping:'Перегрузка', profanity:'Реплика', sound_event:'Звуковое событие', hum:'Гул', hiss:'Шипение', click:'Щелчок', level_jump:'Громкость', stress:'Ударение?', intonation:'Интонация?'};
const statuses = {ready:'Готов к проверке',running:'Идёт проверка',model_loading:'Загрузка моделей',pausing:'Приостанавливается',paused:'Пауза',completed:'Завершено',error:'Ошибка'};
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const shortName = path => path ? path.replaceAll('\\','/').split('/').pop() : '';
const formatSeconds = sec => {sec=Math.max(0,Math.floor(+sec||0));return `${Math.floor(sec/60)}:${String(sec%60).padStart(2,'0')}`};
const isRunning = status => ['running','pausing','model_loading'].includes(status);
const native = () => !!(window.pywebview && window.pywebview.api);
async function invoke(name, ...args){if(!native()) throw Error('Это демонстрация интерфейса. Для работы нужен установленный Audiobook QC.');return window.pywebview.api[name](...args)}
function notify(message){const t=$('toast');t.textContent=String(message);t.classList.remove('hidden');clearTimeout(notify.timer);notify.timer=setTimeout(()=>t.classList.add('hidden'),6500)}
function show(id){state.view=id; $('newView').classList.toggle('hidden',id!=='new');$('projectView').classList.toggle('hidden',id!=='project');$('newNav').classList.toggle('active',id==='new'); $('main').scrollTop=0;renderSidebar()}
function renderSidebar(){
 $('projectCount').textContent=state.projects.length;
 $('projectList').innerHTML = state.projects.length ? state.projects.map(p=>
 `<div class="project-tile ${state.id===p.id&&state.view==='project'?'selected':''}"><button class="project-open" data-id="${esc(p.id)}" title="Открыть проект"><span class="project-name">${esc(p.name)}</span><small class="project-state">${esc(p.ready)}/${esc(p.total)} частей · ${esc(statuses[p.status]||p.status)}</small></button><button class="project-delete" data-delete="${esc(p.id)}" title="Удалить проект">×</button></div>`).join('') : '<div class="small-muted" style="padding:12px">Пока нет проектов</div>';
}
async function refreshProjects(){try{state.projects=await invoke('get_projects');renderSidebar()}catch(e){notify(e.message||e)}}
async function openProject(id){state.id=id;state.selected=null;state.page=0;state.clip=null;show('project');await refreshProject();}
async function refreshProject(){if(!state.id||state.busy)return;try{const p=await invoke('get_project',state.id,state.all); if(state.id!==p.id)return;state.project=p;renderProject(p)}catch(e){notify(e.message||e)}}
function renderProject(p){
 const main=$('main'),scroll=main.scrollTop;
 $('projectTitle').textContent=p.name;
 $('projectSubtitle').textContent=`Текст: ${p.text_name} · ${p.duration_label} · Две локальные модели · Отчёт каждые ${p.part_minutes} мин`;
 $('projectEyebrow').textContent='ПРОЕКТ #'+p.id.slice(0,8).toUpperCase();
 $('statusBadge').textContent=statuses[p.status]||p.status;
 $('progressTitle').textContent=p.status==='completed'?'Анализ завершён':p.status==='paused'?'Проверка приостановлена':'Ход проверки';
 $('progressMessage').textContent=p.message||'—';
 const percent=p.duration?Math.min(100,Math.max(0,100*(p.processed||0)/p.duration)):0;
 $('progressPercent').textContent=Math.round(percent)+'%';$('progressFill').style.width=percent+'%';
 $('progressRange').textContent=`${p.processed_label} / ${p.duration_label}`;
 const done=p.parts.filter(x=>x.status==='done').length;
 $('progressParts').textContent=`${done} / ${p.parts.length} частей`;
 $('readyParts').textContent=`${done}/${p.parts.length}`;
 $('issueCount').textContent=p.all_count;$('priorityCount').textContent=p.high_count;$('repeatCount').textContent=p.repeat_count;
 $('resumeBtn').disabled=isRunning(p.status)||p.status==='completed';$('pauseBtn').disabled=!isRunning(p.status);
 $('partsGrid').innerHTML=p.parts.map(part=>`<div class="part"><div><strong>Часть ${part.index} · ${esc(part.start)}–${esc(part.end)}</strong><small class="${part.status==='done'?'state-ready':part.status==='running'?'state-progress':''}">${part.status==='done'?'✓ Готов Word-отчёт':part.status==='running'?'◷ Обрабатывается':'Ожидает проверки'}</small></div>${part.status==='done'?`<button class="btn secondary" data-report="${part.index}">↓ Word</button>`:''}</div>`).join('');
 renderIssues(p.issues);main.scrollTop=scroll;
}
function renderIssues(issues){
 if(!issues.length){$('issues').innerHTML='<div class="empty">Подтверждённых замечаний пока нет. При необходимости включите «Показывать сомнительные».</div>';$('issuePager').classList.add('hidden');}
 else{
 const per=35,totalPages=Math.ceil(issues.length/per);state.page=Math.min(state.page,totalPages-1);
 $('issues').innerHTML=issues.slice(state.page*per,(state.page+1)*per).map(x=>`<button class="issue ${state.selected===x.id?'selected':''}" data-issue="${esc(x.id)}"><span class="issue-time">${esc(x.time)}</span><span class="kind ${esc(x.kind)}">${esc(labels[x.kind]||x.kind)}</span><span class="issue-text" title="${esc(x.spoken)}">${esc(x.spoken||'—')}</span><span class="issue-text" title="${esc(x.expected)}">${esc(x.expected||'—')}</span><span class="issue-arrow">›</span></button>`).join('');
 $('issuePager').classList.toggle('hidden',totalPages<=1);$('pageLabel').textContent=`Страница ${state.page+1} из ${totalPages}`;$('prevPage').disabled=state.page===0;$('nextPage').disabled=state.page>=totalPages-1;
 }
 const selected=issues.find(x=>x.id===state.selected);
 $('issueDetail').classList.toggle('hidden',!selected);
 if(selected){$('selectedTime').textContent=selected.time;$('spokenText').textContent=selected.spoken||'—';$('expectedText').textContent=selected.expected||'—';$('issueReason').textContent=selected.reason||'Требует прослушивания';
 $('markFixed').disabled=selected.review==='fixed';$('markFalse').disabled=selected.review==='false';}
}
async function choose(kind){try{const path=await invoke('pick_file',kind);if(path){state[kind]=path;$(kind==='audio'?'audioLabel':'textLabel').textContent=shortName(path)}}catch(e){notify(e.message||e)}}
async function create(){if(!state.audio||!state.text)return notify('Выбери аудиозапись и рукопись.');if(state.busy)return;state.busy=true;const btn=$('createBtn');btn.disabled=true;btn.textContent='Создаём проект…';try{const id=await invoke('new_project',state.audio,state.text,+$('minutes').value,$('glossary').value);await refreshProjects();await openProject(id)}catch(e){notify('Не удалось начать проверку: '+(e.message||e))}finally{state.busy=false;btn.disabled=false;btn.innerHTML='Начать проверку <span>→</span>'}}
async function updateJob(operation){if(!state.id)return;try{await invoke(operation,state.id);await refreshProject()}catch(e){notify(e.message||e)}}
async function removeProject(pid){const project=state.projects.find(p=>p.id===pid);if(!project)return;
 if(isRunning(project.status))return notify('Сначала приостановите проверку и дождитесь полной остановки.');
 if(!confirm(`Удалить проект «${project.name}» вместе с его копиями аудио, текста и отчётами?\n\nИсходные файлы вне приложения сохранятся.`))return;
 try{await invoke('delete',pid);if(state.id===pid){state.id=null;state.project=null;state.selected=null;show('new')}await refreshProjects();notify('Проект удалён')}catch(e){notify(e.message||e)}}
async function downloadPart(index){try{await invoke('save_report',state.id,index)}catch(e){notify(e.message||e)}}
async function openReports(){try{await invoke('open_reports',state.id)}catch(e){notify(e.message||e)}}
async function selectIssue(id){state.selected=id;renderIssues(state.project.issues);$('playBtn').disabled=true;$('playBtn').textContent='Подготовка аудио…';$('timeline').value=0;
 try{state.clip=await invoke('load_clip',state.id,id);applyPlayer(state.clip);$('playBtn').disabled=false;$('playBtn').textContent='▶ Слушать';}
 catch(e){$('playBtn').disabled=false;$('playBtn').textContent='▶ Слушать';notify('Не удалось подготовить фрагмент: '+(e.message||e))}}
async function markIssue(status){if(!state.selected)return;try{await invoke('review',state.id,state.selected,status);await refreshProject()}catch(e){notify(e.message||e)}}
function applyPlayer(p){if(!p||!p.loaded)return;state.clip=p;$('playBtn').textContent=p.playing?'Ⅱ Пауза':'▶ Слушать';$('timeline').max=Math.max(1,p.duration);$('timeline').value=Math.min(p.position,p.duration);$('playerTime').textContent=`${formatSeconds(p.position)} / ${formatSeconds(p.duration)}`}
async function playPause(){if(!state.clip||!state.clip.loaded)return;try{applyPlayer(await invoke('toggle_play'))}catch(e){notify(e.message||e)}}
async function seekTo(seconds){if(!state.clip||!state.clip.loaded)return;try{applyPlayer(await invoke('seek',seconds))}catch(e){notify(e.message||e)}}
let tickBusy=false;
async function tick(){if(!native()||tickBusy||state.busy)return;tickBusy=true;try{await refreshProjects();if(state.id&&state.view==='project')await refreshProject();if(state.clip){applyPlayer(await invoke('player_state'))}}finally{tickBusy=false}}
$('newNav').addEventListener('click',()=>{state.id=null;state.selected=null;show('new')});
$('projectList').addEventListener('click',e=>{const del=e.target.closest('[data-delete]'), open=e.target.closest('[data-id]');if(del)removeProject(del.dataset.delete);else if(open)openProject(open.dataset.id)});
$('pickAudio').onclick=()=>choose('audio');$('pickText').onclick=()=>choose('text');$('createBtn').onclick=create;
$('resumeBtn').onclick=()=>updateJob('resume');$('pauseBtn').onclick=()=>updateJob('pause');$('openReportsBtn').onclick=openReports;
$('partsGrid').addEventListener('click',e=>{const part=e.target.closest('[data-report]');if(part)downloadPart(+part.dataset.report)});
$('issues').addEventListener('click',e=>{const issue=e.target.closest('[data-issue]');if(issue)selectIssue(issue.dataset.issue)});
$('prevPage').onclick=()=>{state.page=Math.max(0,state.page-1);renderIssues(state.project.issues)};
$('nextPage').onclick=()=>{state.page++;renderIssues(state.project.issues)};
$('showAll').onchange=e=>{state.all=e.target.checked;state.page=0;refreshProject()};
$('markFixed').onclick=()=>markIssue('fixed');$('markFalse').onclick=()=>markIssue('false');$('markTodo').onclick=()=>markIssue('todo');
$('playBtn').onclick=playPause; $('playerSpeed').onchange=async e=>{try{applyPlayer(await invoke('set_speed',parseFloat(e.target.value)))}catch(err){notify(err.message||err)}};$('back10').onclick=()=>seekTo((state.clip?.position||0)-10);$('forward10').onclick=()=>seekTo((state.clip?.position||0)+10);
$('timeline').addEventListener('change',e=>seekTo(+e.target.value));
window.addEventListener('pywebviewready',async()=>{await tick();show(state.projects.length?'project':'new');if(state.projects.length){state.id=state.projects[0].id;await openProject(state.id)}setInterval(tick,2800)});
// A static preview remains meaningful without a running Python application.
if(!native()){$('projectList').innerHTML='<div class="small-muted" style="padding:12px">Проекты появятся после запуска программы</div>';}
