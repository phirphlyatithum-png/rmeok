const $=id=>document.getElementById(id);
const CATS=["2 ตัวบน","2 ตัวล่าง","3 ตัวตรง","3 ตัวโต๊ด","3 ตัวล่าง"];
let ACCESS_KEY=localStorage.getItem("lottery_key")||sessionStorage.getItem("lottery_key")||"";
let serverOffset=0, clockStarted=false;

function storeKey(k, remember){
  if(remember){ localStorage.setItem("lottery_key",k); sessionStorage.removeItem("lottery_key"); }
  else { sessionStorage.setItem("lottery_key",k); localStorage.removeItem("lottery_key"); }
}
function clearKey(){ localStorage.removeItem("lottery_key"); sessionStorage.removeItem("lottery_key"); }
function money(v){ return Number(v).toLocaleString("th-TH",{minimumFractionDigits:2,maximumFractionDigits:2}); }
function escapeHtml(value){
  return String(value??"").replace(/[&<>"']/g,char=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  })[char]);
}

function showLock(msg){ document.body.classList.add("locked"); $("lockOverlay").style.display="flex"; $("keyMsg").textContent=msg||""; $("keyInput").focus(); }
function hideLock(){ document.body.classList.remove("locked"); $("lockOverlay").style.display="none"; }
function setKeyStatus(v){
  const el=$("keyStatus");
  if(!v||!v.ok){ el.textContent=""; return; }
  if(v.permanent){ el.textContent="🔓 คีย์ถาวร"; return; }
  try{ const d=new Date(v.expires_at); el.textContent="หมดอายุ "+d.toLocaleString("th-TH",{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"}); }
  catch(e){ el.textContent=""; }
}
async function verifyKey(key){
  try{ const r=await fetch("/api/verify?key="+encodeURIComponent(key)); return await r.json(); }
  catch(e){ return null; }
}
async function apiFetch(url,opts={}){
  opts.headers=Object.assign({},opts.headers||{},{"X-Access-Key":ACCESS_KEY});
  const r=await fetch(url,opts);
  if(r.status===403){ ACCESS_KEY=""; clearKey(); showLock("คีย์หมดอายุหรือไม่ถูกต้อง"); throw new Error("forbidden"); }
  return r;
}
async function submitKey(){
  const raw=$("keyInput").value.trim();
  if(!raw){ $("keyMsg").textContent="กรุณาใส่คีย์"; return; }
  $("keyMsg").textContent="กำลังตรวจสอบ...";
  const v=await verifyKey(raw);
  if(v&&v.ok){
    ACCESS_KEY=raw.toUpperCase();
    storeKey(ACCESS_KEY, $("rememberKey").checked);
    setKeyStatus(v); hideLock(); loadDashboard();
  }else{
    $("keyMsg").textContent=(v&&v.message)||"คีย์ไม่ถูกต้องหรือหมดอายุ";
  }
}
$("keySubmit").onclick=submitKey;
$("keyInput").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); submitKey(); } });
$("keyBtn").onclick=()=>{ $("keyInput").value=""; showLock(); };

function grouped(items){
  const map=new Map();
  for(const it of items){
    if(!map.has(it.number)) map.set(it.number,new Array(CATS.length).fill(0));
    const a=map.get(it.number);
    (it.amounts||[]).forEach((v,i)=>{ if(i<a.length) a[i]+=v; });
  }
  return [...map].map(([number,amounts])=>({number,amounts,total:amounts.reduce((a,b)=>a+b,0)}));
}

async function loadDashboard(){
  try{
    const statusResponse=await apiFetch("/api/status");
    if(!statusResponse.ok) throw new Error(`status failed (${statusResponse.status})`);
    const s=await statusResponse.json();
    $("cDates").textContent=s.dates_stored;
    $("cActive").textContent=s.active_keys;
    $("cTotal").textContent=s.total_keys;
    serverOffset=Date.parse(s.server_time)-Date.now();
    startClock(); updateClock();
    const dataResponse=await apiFetch("/api/data");
    if(!dataResponse.ok) throw new Error(`data failed (${dataResponse.status})`);
    const data=await dataResponse.json();
    const rounds=data.rounds||{};
    const rows=$("dateRows");
    const dates=Object.keys(rounds).sort().reverse();
    if(!dates.length){ rows.innerHTML='<tr><td colspan="6" class="muted">ยังไม่มีข้อมูล</td></tr>'; return; }
    rows.innerHTML=dates.map(d=>{
      const r=rounds[d]||{}; const items=r.items||[];
      const total=items.reduce((s,it)=>s+(it.amounts||[]).reduce((a,b)=>a+b,0),0);
      const safeDate=escapeHtml(d);
      return `<tr class="clickable" data-date="${safeDate}"><td>${safeDate}</td><td>${escapeHtml(r.billNo||"-")}</td><td>${escapeHtml(r.customer||"-")}</td><td>${items.length}</td><td><b>${money(total)}</b></td><td><button class="delDate" data-date="${safeDate}" title="ลบงวดนี้">🗑 ลบ</button></td></tr>`;
    }).join("");
    rows.querySelectorAll("tr.clickable").forEach(tr=>{
      tr.onclick=()=>showDetail(tr.dataset.date, rounds[tr.dataset.date]);
    });
    rows.querySelectorAll(".delDate").forEach(btn=>{
      btn.onclick=(e)=>{ e.stopPropagation(); deleteDate(btn.dataset.date); };
    });
  }catch(e){
    $("dateRows").innerHTML='<tr><td colspan="6" class="muted">โหลดข้อมูลไม่สำเร็จ กรุณาลองใหม่</td></tr>';
  }
}

function showDetail(date, r){
  const d=$("detail"); d.classList.add("show");
  $("detailTitle").textContent=`📅 รายละเอียดงวด ${date}  |  บิล ${r.billNo||"-"}  |  ลูกค้า ${r.customer||"-"}`;
  const items=(r.items||[]).slice().sort((a,b)=>a.number.localeCompare(b.number));
  const g=grouped(items);
  const grand=g.reduce((s,x)=>s+x.total,0);
  $("detailBody").innerHTML=
    `<table class="admin"><thead><tr><th>เลข</th>${CATS.map(c=>`<th>${c}</th>`).join("")}<th>รวม</th></tr></thead><tbody>`+
    g.map(x=>`<tr><td><b>${escapeHtml(x.number)}</b></td>${x.amounts.map(v=>`<td>${money(v)}</td>`).join("")}<td><b>${money(x.total)}</b></td></tr>`).join("")+
    `<tr><td colspan="6" style="text-align:right"><b>ยอดรวมทั้งหมด ${money(grand)}</b></td></tr></tbody></table>`;
  d.scrollIntoView({behavior:"smooth"});
}

function updateClock(){
  const t=new Date(Date.now()+serverOffset);
  $("cTime").textContent=t.toLocaleString("th-TH",{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"});
}
function startClock(){
  if(clockStarted) return;
  clockStarted=true;
  setInterval(updateClock, 1000);
}
async function deleteDate(d){
  if(!confirm(`⚠️ ลบงวดวันที่ ${d} ทั้งหมดจริงหรือไม่? (ลบแล้วกู้ไม่ได้)`)) return;
  try{
    const response=await apiFetch("/api/delete_date",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({date:d})
    });
    if(!response.ok) throw new Error(`delete failed (${response.status})`);
    $("detail").classList.remove("show");
    await loadDashboard();
  }catch(e){
    $("dateRows").innerHTML='<tr><td colspan="6" class="muted">ลบงวดไม่สำเร็จ กรุณาลองใหม่</td></tr>';
  }
}

(async function boot(){
  if(ACCESS_KEY){
    const v=await verifyKey(ACCESS_KEY);
    if(v&&v.ok){ setKeyStatus(v); hideLock(); loadDashboard(); return; }
    ACCESS_KEY=""; clearKey();
  }
  showLock();
})();
