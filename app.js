const $=id=>document.getElementById(id);
// 5 ประเภทการแทง
const CATS=["2 ตัวบน","2 ตัวล่าง","3 ตัวตรง","3 ตัวโต๊ด","3 ตัวล่าง"];
const CAT_SHORT=["บน","ล่าง","ตรง","โต๊ด","ล่าง"];
const CAT_IDS=CATS.map((_,i)=>"cat"+(i+1));
const TWO_DIGIT_IDS=CAT_IDS.slice(0,2);
const THREE_DIGIT_IDS=CAT_IDS.slice(2);
const TOD_INPUT_INDEX=2;   // 6/3 กลับ เอาเงินจากช่อง "3 ตัวตรง"
const TOD_RECORD_INDEX=2;  // บันทึกเป็น "3 ตัวตรง"
// state = { currentDate, rounds: { "2026-10-05": {billNo, customer, items[]}, ... } }
let state={ currentDate:"", rounds:{} };
let ACCESS_KEY=localStorage.getItem("lottery_key")||sessionStorage.getItem("lottery_key")||"";
let lastSyncJson="";
function storeKey(k, remember){
  if(remember){ localStorage.setItem("lottery_key",k); sessionStorage.removeItem("lottery_key"); }
  else { sessionStorage.setItem("lottery_key",k); localStorage.removeItem("lottery_key"); }
}
function clearKey(){ localStorage.removeItem("lottery_key"); sessionStorage.removeItem("lottery_key"); }

function uid(){
  if(window.crypto&&crypto.randomUUID) return crypto.randomUUID();
  return "id-"+Date.now()+"-"+Math.random().toString(36).slice(2);
}
function todayStr(){ return new Date().toISOString().slice(0,10); }
function cur(){
  if(!state.rounds[state.currentDate]){
    state.rounds[state.currentDate]={billNo:"0",customer:"",items:[]};
  }
  return state.rounds[state.currentDate];
}
function items(){ return cur().items; }
function permutations(str){
  const set=new Set();
  const arr=str.split("");
  function swap(i,j){ const t=arr[i]; arr[i]=arr[j]; arr[j]=t; }
  function gen(k){
    if(k===1){ set.add(arr.join("")); return; }
    for(let i=0;i<k;i++){ gen(k-1); swap(k%2 ? 0 : i, k-1); }
  }
  gen(arr.length);
  return [...set].sort();
}
function updateCatVisibility(){
  const len=$("number").value.trim().length;
  const show2 = len!==3;
  const show3 = len!==2;
  TWO_DIGIT_IDS.forEach(id=>{
    const label=$(id).closest("label");
    label.style.display = show2 ? "" : "none";
    if(!show2) $(id).value="";
  });
  THREE_DIGIT_IDS.forEach(id=>{
    const label=$(id).closest("label");
    label.style.display = show3 ? "" : "none";
    if(!show3) $(id).value="";
  });
  const todDisabled = len!==3;
  if($("btn6back")) $("btn6back").disabled=todDisabled;
  if($("btn3back")) $("btn3back").disabled=todDisabled;
}
function visibleCatIds(){
  return CAT_IDS.filter(id=>$(id).closest("label").style.display!=="none");
}

// ============ ACCESS KEY ============
function showLock(msg){
  document.body.classList.add("locked");
  $("lockOverlay").style.display="flex";
  $("keyMsg").textContent=msg||"";
  $("keyInput").focus();
}
function hideLock(){
  document.body.classList.remove("locked");
  $("lockOverlay").style.display="none";
}
function setKeyStatus(v){
  const el=$("keyStatus");
  if(!v||!v.ok){ el.textContent=""; return; }
  if(v.permanent){ el.textContent="🔓 คีย์ถาวร"; return; }
  try{
    const d=new Date(v.expires_at);
    el.textContent="หมดอายุ "+d.toLocaleString("th-TH",{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
  }catch(e){ el.textContent=""; }
}
async function verifyKey(key){
  try{
    const r=await fetch("/api/verify?key="+encodeURIComponent(key));
    return await r.json();
  }catch(e){ return null; }
}
// fetch ทุกครั้งแนบคีย์ใน header; ถ้าเซิร์ฟเวอร์ตอบ 403 → แสดงหน้าใส่คีย์ใหม่
async function apiFetch(url,opts={}){
  opts.headers=Object.assign({},opts.headers||{},{"X-Access-Key":ACCESS_KEY});
  const r=await fetch(url,opts);
  if(r.status===403){
    ACCESS_KEY=""; clearKey();
    showLock("คีย์หมดอายุหรือไม่ถูกต้อง กรุณาใส่คีย์ใหม่");
    throw new Error("forbidden");
  }
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
    setKeyStatus(v);
    hideLock();
    load();
  }else{
    $("keyMsg").textContent=(v&&v.message)||"คีย์ไม่ถูกต้องหรือหมดอายุ";
  }
}
// ============ END ACCESS KEY ============

async function load(){
  let data=null;
  try{
    const r=await apiFetch("/api/data");
    if(r.ok) data=await r.json();
  }catch(e){ return; }
  if(data && Array.isArray(data.items)){
    const d=data.date||todayStr();
    state={ currentDate:d, rounds:{ [d]:{ billNo:data.billNo||"0", customer:data.customer||"", items:data.items||[] } } };
  }else if(data && data.rounds){
    state=data;
    if(!state.currentDate) state.currentDate=todayStr();
  }else{
    state={ currentDate:todayStr(), rounds:{} };
  }
  Object.values(state.rounds).forEach(r=>{
    (r.items||[]).forEach(it=>{
      if(!Array.isArray(it.amounts)) it.amounts=[];
      while(it.amounts.length<CATS.length) it.amounts.push(0);
    });
  });
  lastSyncJson=JSON.stringify(state);
  refreshRoundFields();
  updateCatVisibility();
  render();
}
function refreshRoundFields(){
  $("roundDate").value=state.currentDate;
  $("billNo").value=cur().billNo||"0";
  $("customer").value=cur().customer||"";
}
async function save(){
  cur().billNo=$("billNo").value;
  cur().customer=$("customer").value;
  try{
    await apiFetch("/api/data",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(state)
    });
    lastSyncJson=JSON.stringify(state);
    $("saveState").textContent="บันทึกแล้ว "+new Date().toLocaleTimeString("th-TH");
  }catch(e){
    $("saveState").textContent="บันทึกไม่ได้";
  }
}
function switchDate(d){
  if(!d) return;
  state.currentDate=d;
  refreshRoundFields();
  updateCatVisibility();
  render();
  save();
}
function clearInputsAndFocus(){
  ["number",...CAT_IDS].forEach(id=>$(id).value="");
  updateCatVisibility();
  render();
  save();
  $("number").focus();
  setTimeout(()=>{ $("number").focus(); }, 0);
}
function addItem(){
  const number=$("number").value.trim();
  if(!/^\d{2,3}$/.test(number)){
    alert("กรุณาใส่เลขให้ครบ 2 หรือ 3 หลัก");
    $("number").focus();
    return;
  }
  const amounts=CAT_IDS.map(id=>Number($(id).value)||0);
  if(amounts.every(v=>v===0)){
    alert("กรุณาใส่จำนวนเงินอย่างน้อย 1 ช่อง");
    return;
  }
  items().push({ id:uid(), number, amounts, time:new Date().toISOString() });
  clearInputsAndFocus();
}
function addTod(kind){
  const number=$("number").value.trim();
  if(!/^\d{3}$/.test(number)){
    alert("กรุณาใส่เลข 3 หลักก่อน (สำหรับ 6 กลับ / 3 กลับ)");
    $("number").focus();
    return;
  }
  const amount=Number($(CAT_IDS[TOD_INPUT_INDEX]).value)||0;
  if(amount<=0){
    alert("กรุณาใส่จำนวนเงินที่ช่อง 3 ตัวตรง ก่อนกดปุ่ม 6 กลับ / 3 กลับ");
    $(CAT_IDS[TOD_INPUT_INDEX]).focus();
    return;
  }
  const perms=permutations(number);
  if(kind===6 && perms.length!==6){
    alert("เลข "+number+" มีตัวเลขซ้ำกัน → สลับได้แค่ "+perms.length+" ชุด ไม่ใช่ 6 กลับ\nใช้ปุ่ม 3 กลับแทน");
    return;
  }
  if(kind===3 && perms.length!==3){
    alert("เลข "+number+" สลับได้ "+perms.length+" ชุด ไม่ใช่รูปแบบ 3 กลับ (ต้องมีเลขซ้ำ 1 คู่ เช่น 112)");
    return;
  }
  const now=new Date().toISOString();
  const groupId=uid();
  perms.forEach(p=>{
    const amounts=new Array(CATS.length).fill(0);
    amounts[TOD_RECORD_INDEX]=amount;
    items().push({ id:uid(), number:p, amounts, time:now, todGroup:groupId, todBase:number, todKind:kind });
  });
  clearInputsAndFocus();
}
function grouped(){
  const map=new Map();
  for(const item of items()){
    if(!map.has(item.number)) map.set(item.number,new Array(CATS.length).fill(0));
    const a=map.get(item.number);
    item.amounts.forEach((v,i)=>{ if(i<a.length) a[i]+=v; });
  }
  return [...map].map(([number,amounts])=>({
    number,amounts,total:amounts.reduce((a,b)=>a+b,0)
  }));
}
function money(v){
  return Number(v).toLocaleString("th-TH",{minimumFractionDigits:2,maximumFractionDigits:2});
}
function rowHtml(item){
  const parts=item.amounts
    .map((v,i)=>`<b class="cat c${i}">${CAT_SHORT[i]}</b> ${money(v)}`)
    .filter((_,i)=>item.amounts[i]>0);
  return `<div class="row" data-number="${item.number}">
    <span class="num">${item.number}</span>
    <span class="vals">${parts.join(" · ")||"-"}</span>
    <span class="sum">รวม ${money(item.total)}</span>
  </div>`;
}
function render(){
  const groups=grouped();
  const total=groups.reduce((sum,x)=>sum+x.total,0);
  $("grandTotal").textContent=money(total);
  $("roundTitle").textContent="งวดประจำวันที่ "+(state.currentDate||"-");
  const byNum=(a,b)=>a.number.localeCompare(b.number);
  $("list2").innerHTML=
    groups.filter(x=>x.number.length===2).sort(byNum).map(rowHtml).join("")||"<div>ยังไม่มีรายการ</div>";
  $("list3").innerHTML=
    groups.filter(x=>x.number.length===3).sort(byNum).map(rowHtml).join("")||"<div>ยังไม่มีรายการ</div>";
  const histGroups=[];
  const seen=new Set();
  items().slice().reverse().forEach(item=>{
    const key=item.todGroup||item.id;
    if(seen.has(key)){ histGroups.find(g=>g.key===key).items.push(item); return; }
    seen.add(key);
    histGroups.push({key, items:[item]});
  });
  $("history").innerHTML=histGroups.map(g=>{
    const first=g.items[0];
    const gTotal=g.items.reduce((s,it)=>s+it.amounts.reduce((a,b)=>a+b,0),0);
    let numText=first.number;
    if(first.todGroup){
      numText=`${first.todBase} <span class="todtag">(${first.todKind} กลับ ×${g.items.length})</span>`;
    }
    const parts=first.amounts
      .map((v,i)=>`<b class="cat c${i}">${CAT_SHORT[i]}</b> ${money(v)}`)
      .filter((_,i)=>first.amounts[i]>0);
    return `<div class="row" data-id="${g.key}">
      <span class="num">${numText}</span>
      <span class="vals">${parts.join(" · ")||"-"}</span>
      <span class="sum">รวม ${money(gTotal)}</span>
    </div>`;
  }).join("")||"<div>ยังไม่มีรายการ</div>";
}
$("addBtn").onclick=addItem;
$("btn6back").onclick=()=>addTod(6);
$("btn3back").onclick=()=>addTod(3);
$("number").addEventListener("keydown",e=>{
  const okKeys=["Backspace","Delete","ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Tab","Enter","Home","End"];
  if(okKeys.includes(e.key)||e.ctrlKey||e.metaKey) return;
  if(!/^\d$/.test(e.key)) e.preventDefault();
});
$("number").addEventListener("input",()=>{
  const el=$("number");
  const cleaned=el.value.replace(/\D/g,"").slice(0,3);
  if(cleaned!==el.value) el.value=cleaned;
  updateCatVisibility();
});
["number",...CAT_IDS].forEach(id=>{
  $(id).addEventListener("keydown",e=>{
    if(e.key!=="Enter") return;
    e.preventDefault();
    if(id==="number"){
      const vis=visibleCatIds();
      $(vis[0]||"cat1").focus();
      return;
    }
    const idx=CAT_IDS.indexOf(id);
    const laterVis=CAT_IDS.slice(idx+1).filter(cid=>$(cid).closest("label").style.display!=="none");
    if(laterVis.length>0) $(laterVis[0]).focus();
    else addItem();
  });
});
$("history").onclick=e=>{
  const row=e.target.closest(".row");
  if(!row)return;
  if(confirm("ลบรายการนี้หรือไม่?")){
    const key=row.dataset.id;
    const arr=items();
    state.rounds[state.currentDate].items=arr.filter(x=>x.id!==key && x.todGroup!==key);
    render();save();
  }
};
["list2","list3"].forEach(id=>{
  $(id).onclick=e=>{
    const row=e.target.closest(".row");
    if(!row)return;
    if(confirm("ลบข้อมูลของเลข "+row.dataset.number+" ทั้งหมดหรือไม่?")){
      const arr=items();
      state.rounds[state.currentDate].items=arr.filter(x=>x.number!==row.dataset.number);
      render();save();
    }
  };
});
$("roundDate").onchange=()=>switchDate($("roundDate").value);
$("useDate").onclick=()=>switchDate($("roundDate").value);
$("newRound").onclick=()=>{
  if(confirm("เริ่มงวดใหม่: ไปวันที่ปัจจุบันและล้างรายการของวันนั้นหรือไม่?")){
    const t=todayStr();
    state.currentDate=t;
    state.rounds[t]={billNo:cur().billNo||"0",customer:cur().customer||"",items:[]};
    refreshRoundFields();
    updateCatVisibility();
    render();save();
  }
};
$("clearAll").onclick=()=>{
  if(confirm("ล้างรายการของงวดนี้ทั้งหมดจริงหรือไม่?")){
    cur().items=[];
    render();save();
  }
};
$("billNo").onchange=save;
$("customer").onchange=save;
$("exportCsv").onclick=()=>{
  let csv="เลข,"+CATS.join(",")+",รวม\n";
  grouped().forEach(x=>{ csv+=`${x.number},${x.amounts.join(",")},${x.total}\n`; });
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob(["\ufeff"+csv],{type:"text/csv;charset=utf-8"}));
  a.download=`number-tally-${state.currentDate||"data"}.csv`;
  a.click();
};

// ============ KEY UI wiring ============
$("keySubmit").onclick=submitKey;
$("keyInput").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); submitKey(); } });
$("keyBtn").onclick=()=>{ $("keyInput").value=""; showLock(); };

// ===== Auto-sync: ดึงข้อมูลล่าสุดทุก 4 วิ คนอื่นเพิ่มรายการ เราเห็นเลย ไม่ต้องรีเฟรช =====
async function autoSync(){
  if(!ACCESS_KEY) return;
  try{
    const r=await apiFetch("/api/data");
    if(!r.ok) return;
    const data=await r.json();
    const j=JSON.stringify(data);
    if(j===lastSyncJson) return;                       // ข้อมูลเท่าเดิม
    if(JSON.stringify(state)!==lastSyncJson) return;   // เรากำลังมีข้อมูลที่ยังไม่บันทึก → ข้ามก่อน
    // เซิร์ฟเวอร์มีข้อมูลใหม่ → อัปเดตหน้า เก็บค่าช่องที่กำลังพิมพ์ไว้
    const focused=document.activeElement?document.activeElement.id:null;
    const vals={};
    ["number","billNo","customer",...CAT_IDS].forEach(id=>{ if($(id)) vals[id]=$(id).value; });
    state=data;
    Object.values(state.rounds).forEach(r=>{
      (r.items||[]).forEach(it=>{
        if(!Array.isArray(it.amounts)) it.amounts=[];
        while(it.amounts.length<CATS.length) it.amounts.push(0);
      });
    });
    refreshRoundFields();
    updateCatVisibility();
    render();
    Object.keys(vals).forEach(id=>{ if($(id)) $(id).value=vals[id]; });
    if(focused && $(focused)){ $(focused).focus(); try{$(focused).setSelectionRange(999,999);}catch(e){} }
    lastSyncJson=j;
  }catch(e){}
}
setInterval(autoSync, 4000);

// boot: ถ้ามีคีย์เก็บไว้ ให้ตรวจสอบ แล้วค่อยโหลดข้อมูล
(async function boot(){
  if(ACCESS_KEY){
    const v=await verifyKey(ACCESS_KEY);
    if(v&&v.ok){ setKeyStatus(v); hideLock(); load(); return; }
    ACCESS_KEY=""; clearKey();
  }
  showLock();
})();
