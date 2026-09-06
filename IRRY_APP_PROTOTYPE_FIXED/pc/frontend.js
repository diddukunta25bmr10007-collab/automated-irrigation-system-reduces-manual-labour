
const API = {
  status:"/api/status",
  sensorData:"/api/sensor-data",
  start:"/api/start",
  stop:"/api/stop",
  estop:"/api/estop",
  schedule:"/api/schedule",
  cancelSchedule:"/api/schedule/cancel",
  history:"/api/history",
  clearHistory:"/api/history/clear"
};
const DEFAULT_BEDS=3, MAX_BEDS=3, DEFAULT_DURATION=2, MAX_DURATION_MINUTES=180;
let selectedBeds=[], completedBeds=new Set(), irrigationHistory=[];
let backendState=null, backendConnected=false, polling=null;

const el=id=>document.getElementById(id);
const text=(id,v)=>{if(el(id)) el(id).textContent=v};
const bedName=n=>`Bed ${n}`;

function message(m){
  const x=el("message"); if(!x){console.log(m);return}
  x.textContent=m; x.classList.add("show");
  clearTimeout(window._msg); window._msg=setTimeout(()=>x.classList.remove("show"),3500);
}
function setConnection(ok){
  backendConnected=ok;
  const c=el("connectionChip"); if(!c)return;
  c.textContent=ok?"● Backend connected":"● Backend offline";
  c.classList.toggle("live",ok);
}
function setStatus(s,m=""){
  text("systemStatus",s); text("statusMessage",m);
  const d=el("statusDot"); if(d)d.className="dot "+String(s).toLowerCase().replace(/\s+/g,"-");
}
function save(){
  localStorage.setItem("irry_completed",JSON.stringify([...completedBeds]));
  localStorage.setItem("irry_history",JSON.stringify(irrigationHistory.slice(0,30)));
}
function load(){
  try{
    completedBeds=new Set((JSON.parse(localStorage.getItem("irry_completed")||"[]")||[]).map(Number));
    irrigationHistory=JSON.parse(localStorage.getItem("irry_history")||"[]")||[];
  }catch{completedBeds=new Set();irrigationHistory=[]}
}
function durationFor(b){
  const i=document.querySelector(`.bed[data-bed="${b}"] .bed-dur`);
  const n=Number(i?.value);
  if(!Number.isFinite(n)||n<=0||n>MAX_DURATION_MINUTES){message(`Enter a valid duration for ${bedName(b)} (0-${MAX_DURATION_MINUTES} min).`);i?.focus();return null}
  return n;
}
function createBedElement(n){
  const d=document.createElement("div"); d.className="bed"; d.dataset.bed=n;
  d.innerHTML=`<div class="bed-number">${bedName(n)}</div><div class="bed-state">Pending</div>
  <div class="bed-duration-row"><span>Minutes</span><input class="bed-dur" type="number" min="0.1" max="${MAX_DURATION_MINUTES}" step="0.5" value="${DEFAULT_DURATION}"></div><div class="bed-check"></div>`;
  d.addEventListener("click",e=>{if(e.target.matches("input"))return;selectBed(n)});
  return d;
}
function initializeBeds(){
  const c=el("bedControls"); if(!c)return;
  c.innerHTML="";
  for(let n=1;n<=DEFAULT_BEDS;n++)c.appendChild(createBedElement(n));
  updateUI();
}
function selectBed(n){
  if(backendState?.running){message("Stop irrigation before changing beds.");return}
  if(completedBeds.has(n)){message(`${bedName(n)} is complete. Reset progress first.`);return}
  selectedBeds=selectedBeds.includes(n)?selectedBeds.filter(x=>x!==n):[...selectedBeds,n];
  updateUI();
}
function clearSelection(){
  if(backendState?.running){message("Stop irrigation before clearing selection.");return}
  selectedBeds=[];updateUI();message("Selection cleared.");
}
function addBed(){
  const c=el("bedControls"); const count=c?.querySelectorAll(".bed").length||0;
  if(count>=MAX_BEDS){message(`Maximum ${MAX_BEDS} beds reached.`);return}
  c.appendChild(createBedElement(count+1));updateUI();message(`${bedName(count+1)} added.`);
}
function updateUI(){
  const current=Number(backendState?.current_bed||0);
  document.querySelectorAll(".bed").forEach(d=>{
    const n=Number(d.dataset.bed), done=completedBeds.has(n), sel=selectedBeds.includes(n), active=current===n&&backendState?.running;
    d.classList.toggle("selected",sel&&!done); d.classList.toggle("active",active); d.classList.toggle("done",done);
    textState(d,done?"✓ Done":active?"Irrigating":sel?`Selected #${selectedBeds.indexOf(n)+1}`:"Pending");
    const check=d.querySelector(".bed-check"); if(check)check.textContent=done?"✓":active?"💧":sel?"✓":"";
  });
  text("selectedBeds",selectedBeds.length?`${selectedBeds.length} selected • ${selectedBeds.map(bedName).join(", ")}`:"None selected");
  const total=document.querySelectorAll(".bed").length||DEFAULT_BEDS;
  const done=[...completedBeds].filter(n=>document.querySelector(`.bed[data-bed="${n}"]`)).length;
  const pct=Math.round(done/total*100);
  text("completedBeds",`${done} / ${total}`);text("remainingBeds",total-done);text("progressPercent",`${pct}%`);
  if(el("progressBar"))el("progressBar").style.width=pct+"%";
  text("selectedCount",selectedBeds.length);
  const active=backendState?.running?Number(backendState.current_bed||0):0;
  text("currentBedTop",active?bedName(active):"No bed active");text("currentBedStat",active?bedName(active):"—");
  text("valveStat",active?"Open":"Closed");text("valveStatus",active?"OPEN":"CLOSED");
  text("countdownTimer",backendState?.time||"00:00");text("timerStat",backendState?.time||"00:00");
  renderHistory();
}
function textState(d,s){const x=d.querySelector(".bed-state");if(x)x.textContent=s}

async function startIrrigation(){
  if(!backendConnected){message("Backend is offline. Start backend_server.py first.");return}
  if(backendState?.running){message("Irrigation is already running.");return}
  if(!selectedBeds.length){message("Select at least one bed.");return}
  const durations={};
  for(const n of selectedBeds){const m=durationFor(n);if(m===null)return;durations[n]=m*60}
  try{
    const r=await fetch(API.start,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({beds:selectedBeds,durations})});
    const data=await r.json(); if(!r.ok||!data.ok)throw Error(data.message||"Start rejected");
    setStatus("Running",data.message);message("Irrigation started.");
  }catch(e){message(e.message||"Unable to start irrigation.");await refresh()}
}
async function stopIrrigation(){
  if(!backendConnected){message("Backend is offline.");return}
  try{await post(API.stop,{});message("Irrigation stopped.");await refresh()}catch(e){message(e.message)}
}
async function emergencyStop(){
  if(!backendConnected){message("Backend is offline.");return}
  try{await post(API.estop,{});message("⛔ Emergency stop activated.");await refresh()}catch(e){message(e.message)}
}
async function post(url,body){
  const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(d.message||"Request failed");
  return d;
}
async function scheduleIrrigation(){
  if(!backendConnected){message("Backend is offline.");return}
  if(!selectedBeds.length){message("Select beds first.");return}
  const date=el("scheduleDate")?.value,time=el("scheduleTime")?.value,dur=Number(el("scheduleDuration")?.value);
  if(!date||!time||!Number.isFinite(dur)||dur<=0){message("Enter date, time and duration.");return}
  if(new Date(`${date}T${time}`).getTime()<=Date.now()){message("Schedule time must be in the future.");return}
  try{
    const d=await post(API.schedule,{beds:selectedBeds,date,time,duration:dur});
    message(d.message||"Schedule created.");await loadSchedules();
  }catch(e){message(e.message)}
}
async function cancelSchedule(){
  if(!backendConnected){message("Backend is offline.");return}
  try{const d=await post(API.cancelSchedule,{});message(d.message||"Schedule cancelled.");await loadSchedules()}catch(e){message(e.message)}
}
async function loadSchedules(){
  try{
    const r=await fetch(API.schedule);const a=await r.json();
    const s=el("scheduleStatus");
    if(s)s.textContent=a.length?`Pending: ${a.map(x=>`${bedName(x.bed_number)} • ${x.start_time} • ${Math.round(x.duration/60)} min`).join(" | ")}`:"No irrigation scheduled.";
  }catch{}
}
async function resetBedStatus(){
  if(backendState?.running){message("Stop irrigation first.");return}
  completedBeds.clear();selectedBeds=[];save();updateUI();message("Bed completion status reset.");
}
async function clearHistory(){
  if(!backendConnected){message("Backend is offline.");return}
  try{await post(API.clearHistory,{});irrigationHistory=[];save();renderHistory();message("History cleared.")}catch(e){message(e.message)}
}
function renderHistory(){
  const c=el("irrigationHistory");if(!c)return;c.innerHTML="";
  if(!irrigationHistory.length){c.innerHTML='<div class="empty">No irrigation history yet.</div>';return}
  irrigationHistory.forEach(h=>{
    const d=document.createElement("div");d.className="history-item";
    d.innerHTML=`<span>${h.label||h.status||"Irrigation"}</span><span>${h.time||h.started_at||""}</span>`;c.appendChild(d)
  });
}
function syncHistoryFromBackend(rows){
  const out=[];
  rows.forEach(r=>{
    const watered=(r.beds_watered||"").split(",").filter(Boolean);
    watered.forEach(b=>out.push({label:`${bedName(b)} — Completed`,time:r.ended_at||r.started_at}));
    const skipped=(r.beds_skipped||"").split(",").filter(Boolean);
    skipped.forEach(b=>out.push({label:`${bedName(b)} — Skipped`,time:r.ended_at||r.started_at}));
  });
  if(out.length){
    irrigationHistory=out.slice(0,30);
    const completedFromBackend=[];
    rows.forEach(r=>{
      (r.beds_watered||"").split(",").filter(Boolean).forEach(b=>completedFromBackend.push(Number(b)));
    });
    completedFromBackend.filter(Number.isInteger).forEach(b=>completedBeds.add(b));
    save();
  }
}
async function refresh(){
  try{
    const r=await fetch(API.status,{cache:"no-store"});if(!r.ok)throw Error();
    backendState=await r.json();setConnection(true);
    const s=backendState.status||"Idle";setStatus(s,backendState.message||"");
    updateUI();
    const sensor=await fetch(API.sensorData,{cache:"no-store"}).then(x=>x.json()).catch(()=>null);
    if(sensor){
      text("soilMoisture",sensor.soil_moisture!=null?`${sensor.soil_moisture}%`:"Unavailable");
      text("temperature",sensor.temperature!=null?`${sensor.temperature} °C`:"Unavailable");
      text("humidity",sensor.humidity!=null?`${sensor.humidity}%`:"Unavailable");
      text("weatherStatus",sensor.weather||"Unavailable");
      text("rainStatus",sensor.rain_expected==null?"Unavailable":sensor.rain_expected?"Rain expected":"No rain expected");
      text("lastUpdated",`Updated ${new Date().toLocaleTimeString()}`);
    }
    const rows=await fetch(API.history+"?limit=30").then(x=>x.json()).catch(()=>[]);
    if(Array.isArray(rows))syncHistoryFromBackend(rows);
    await loadSchedules();
    updateUI();
  }catch{
    setConnection(false);setStatus("Offline","Backend unavailable — start backend_server.py.");updateUI();
  }
}
document.addEventListener("DOMContentLoaded",()=>{
  load();initializeBeds();renderHistory();
  el("addBedBtn")?.addEventListener("click",addBed);
  el("clearBeds")?.addEventListener("click",clearSelection);
  el("clearProgressBtn")?.addEventListener("click",resetBedStatus);
  el("startIrrigation")?.addEventListener("click",startIrrigation);
  el("stopIrrigation")?.addEventListener("click",stopIrrigation);
  el("emergencyStop")?.addEventListener("click",emergencyStop);
  el("scheduleIrrigation")?.addEventListener("click",scheduleIrrigation);
  el("cancelSchedule")?.addEventListener("click",cancelSchedule);
  el("clearHistory")?.addEventListener("click",clearHistory);
  refresh();
  // Fast loop: only the live controller state.
  setInterval(async()=>{
    try{
      const r=await fetch(API.status,{cache:"no-store"});
      if(!r.ok) throw Error();
      backendState=await r.json(); setConnection(true);
      setStatus(backendState.status||"Idle",backendState.message||"");
      updateUI();
    }catch{
      setConnection(false);
      setStatus("Offline","Backend unavailable — start backend_server.py.");
      updateUI();
    }
  },500);
  // Slower loop: sensors, history and schedules.
  setInterval(refresh,5000);
});
