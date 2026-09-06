let base=localStorage.getItem("irry_server")||location.origin;let state=null,sel=new Set([1,2,3]);const $=id=>document.getElementById(id);
function api(path){return base.replace(/\/$/,"")+path}
async function get(path,opts){const r=await fetch(api(path),opts);const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.message||"Request failed");return d}
function message(s){$("msg").textContent=s}

// Build the 3 bed cards ONCE. The duration <input> is created here and
// never recreated afterwards, so typing in it survives the 1s poll loop.
function renderBeds(){
  const c=$("beds");c.innerHTML="";
  for(let n=1;n<=3;n++){
    const d=document.createElement("div");
    d.className="bed";d.dataset.bed=n;
    d.innerHTML=`<h3>Bed ${n}</h3><div class="state">PENDING</div><div class="moist" style="font-size:10px;color:#9eb5ac;margin-top:6px">Moisture: --%</div><input class="dur" data-bed="${n}" type="number" min="0.1" max="180" step="0.5" value="2">`;
    d.onclick=e=>{if(e.target.matches("input"))return;if(state?.running)return message("Stop irrigation before changing beds.");sel.has(n)?sel.delete(n):sel.add(n);syncBeds()};
    c.appendChild(d);
  }
  syncBeds();
}

// Called on every refresh / selection change. Only updates class + text +
// disabled state on the EXISTING cards - never touches the input's value,
// so a value the user is typing is never overwritten out from under them.
function syncBeds(){
  for(let n=1;n<=3;n++){
    const d=document.querySelector(`.bed[data-bed="${n}"]`);if(!d)continue;
    const active=!!(state?.running&&Number(state.current_bed)===n);
    d.className="bed"+(sel.has(n)?" sel":"")+(active?" active":"");
    d.querySelector(".state").textContent=active?"IRRIGATING":sel.has(n)?"SELECTED":"PENDING";
    d.querySelector(".moist").textContent="Moisture: "+(state?.moisture?.["bed"+n]??"--")+"%";
    const inp=d.querySelector(".dur");
    inp.disabled=!!state?.running;
  }
}

async function refresh(){try{state=await get("/api/status");const m=await get("/api/sensor-data");$("conn").textContent="● Backend connected";$("conn").classList.add("live");$("status").textContent=state.status||"Idle";$("active").textContent=state.current_bed?`Bed ${state.current_bed}`:"—";$("timer").textContent=state.time||"00:00";$("pump").textContent=state.pump||"OFF";$("soil").textContent=m.soil_moisture!=null?m.soil_moisture+"%":"Unavailable";$("temp").textContent=m.temperature!=null?m.temperature+" °C":"Unavailable";$("hum").textContent=m.humidity!=null?m.humidity+"%":"Unavailable";$("weather").textContent=m.weather||"Unavailable";$("rain").textContent=m.rain_expected==null?"Unavailable":m.rain_expected?"Rain expected":"No rain expected";$("threshold").textContent=(state.moisture_threshold??"--")+"%";const b=state.current_bed?Number(state.current_bed):0;$("bar").style.width=((b?Number(state.water_progress?.["bed"+b]||0):0)*100)+"%";syncBeds()}catch(e){$("conn").textContent="● Backend offline";$("conn").classList.remove("live")}}
$("server").value=base;$("save").onclick=()=>{base=$("server").value.trim().replace(/\/$/,"")||location.origin;localStorage.setItem("irry_server",base);message("Server address saved.");refresh()};
$("all").onclick=()=>{sel=new Set([1,2,3]);syncBeds()};$("clear").onclick=()=>{if(!state?.running){sel.clear();syncBeds()}};
$("start").onclick=async()=>{if(!sel.size)return message("Select at least one bed.");const durations={};for(const n of sel){const v=Number(document.querySelector(`.dur[data-bed="${n}"]`)?.value);if(!Number.isFinite(v)||v<=0||v>180)return message(`Invalid duration for Bed ${n} (0-180 min).`);durations[n]=v*60}try{await get("/api/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({beds:[...sel],durations})});message("Irrigation started.");refresh()}catch(e){message(e.message)}};
$("stop").onclick=async()=>{try{await get("/api/stop",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});message("Irrigation stopped.");refresh()}catch(e){message(e.message)}};
$("estop").onclick=async()=>{try{await get("/api/estop",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});message("Emergency stop activated.");refresh()}catch(e){message(e.message)}};
if("serviceWorker" in navigator)navigator.serviceWorker.register("/mobile/sw.js").catch(()=>{});renderBeds();refresh();setInterval(refresh,1000);
