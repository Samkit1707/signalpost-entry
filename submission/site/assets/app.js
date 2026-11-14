(function(){
// fold: lower case, æ→ae ø→o å→a, accents and everything but letters/digits dropped (so spaces never matter)
const fold=v=>String(v??'').toLowerCase().replace(/æ/g,'ae').replace(/ø/g,'o').replace(/å/g,'a').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]/g,'');
const digits=v=>String(v??'').replace(/\D/g,'');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safe=u=>/^https?:\/\//i.test(String(u||''))?u:null;
const MAX=3;
function selected(){try{return JSON.parse(sessionStorage.getItem('sp-compare')||'[]')}catch(e){return []}}
function store(v){try{sessionStorage.setItem('sp-compare',JSON.stringify(v))}catch(e){}}
// Index: search, sort, filters, compare tray (the list is pre-rendered, sorted by most data found; this only re-orders/filters).
const list=document.getElementById('companies');
if(list){
  const rows=[...list.querySelectorAll('li[data-org]')],q=document.getElementById('q'),st=document.getElementById('status'),
        sort=document.getElementById('sort'),place=document.getElementById('place'),ind=document.getElementById('industry'),area=document.getElementById('area'),
        count=document.getElementById('count'),tray=document.getElementById('tray-names'),go=document.getElementById('compare-go'),clear=document.getElementById('compare-clear');
  let picks=selected().filter(o=>rows.some(r=>r.dataset.org===o));
  const params=new URLSearchParams(location.search);
  if(params.get('q'))q.value=params.get('q');if(params.get('area'))area.value=params.get('area');
  function order(){const k=sort.value;const sorted=[...rows].sort((a,b)=>k==='name'?a.dataset.label.localeCompare(b.dataset.label,'nb'):
      k==='revenue'?(parseFloat(b.dataset.revenue||'-1e18')-parseFloat(a.dataset.revenue||'-1e18')):(parseFloat(b.dataset.score)-parseFloat(a.dataset.score))||a.dataset.label.localeCompare(b.dataset.label,'nb'));
    sorted.forEach(r=>list.appendChild(r))}
  function apply(){
    const text=fold(q.value);let shown=0;
    rows.forEach(r=>{const ok=(!text||r.dataset.name.includes(text))&&(!st.value||r.dataset.status===st.value)&&(!place.value||r.dataset.place===place.value)
      &&(!ind.value||r.dataset.division===ind.value)&&(!area.value||(' '+r.dataset.areas+' ').includes(' '+area.value+' '));
      r.hidden=!ok;if(ok)shown++});
    count.textContent=shown+' of '+rows.length+' companies';
  }
  function renderTray(){
    rows.forEach(r=>{const box=r.querySelector('input[type=checkbox]');box.checked=picks.includes(r.dataset.org);box.disabled=!box.checked&&picks.length>=MAX});
    tray.textContent=picks.length?picks.map(o=>rows.find(r=>r.dataset.org===o).dataset.label).join(' · '):'Pick 2–3 companies to compare';
    go.disabled=picks.length<2;clear.hidden=!picks.length;store(picks);
  }
  list.addEventListener('change',e=>{const box=e.target;if(box.type!=='checkbox')return;const org=box.closest('li').dataset.org;
    picks=box.checked?[...new Set([...picks,org])].slice(0,MAX):picks.filter(o=>o!==org);renderTray()});
  go.addEventListener('click',()=>{location.href='compare.html?orgs='+picks.join(',')});
  clear.addEventListener('click',()=>{picks=[];renderTray()});
  q.addEventListener('input',apply);[st,place,ind,area].forEach(el=>el.addEventListener('change',apply));sort.addEventListener('change',()=>{order();apply()});
  order();apply();renderTray();
}
// Compare view: the page is pre-rendered (all companies, key facts); with ?orgs= this adds a side-by-side view on top.
const cmp=document.getElementById('compare');
if(cmp){
  const COMPANIES=window.SP_COMPANIES||[],byOrg=Object.fromEntries(COMPANIES.map(c=>[c[0],c[1]])),loaded={};
  window.SP_LOAD=(org,data)=>{loaded[org]=data;render()};
  let orgs=(new URLSearchParams(location.search).get('orgs')||'').split(',').map(digits).filter(o=>byOrg[o]).slice(0,MAX);
  if(!orgs.length)orgs=selected().filter(o=>byOrg[o]).slice(0,MAX);
  const pickers=[...document.querySelectorAll('.pick')];
  document.getElementById('pickers').hidden=false;
  pickers.forEach((input,i)=>{input.value=orgs[i]?byOrg[orgs[i]]+' — '+orgs[i]:'';
    input.addEventListener('change',()=>{const m=digits(input.value.split('—').pop());const next=pickers.map((p,j)=>j===i?m:orgs[j]).filter(o=>o&&byOrg[o]);
      orgs=[...new Set(next)].slice(0,MAX);store(orgs);history.replaceState(null,'','?orgs='+orgs.join(','));load()})});
  function load(){orgs.forEach(o=>{if(loaded[o])return;const s=document.createElement('script');s.src='data/'+o+'.js';
    s.onerror=()=>{loaded[o]={org:o,name:byOrg[o],facts:[],error:true};render()};document.head.appendChild(s)});render()}
  function cell(f){if(!f)return '<span class="missing">Not available</span><span class="reason">Not checked for this company</span>';
    const val=f.link&&safe(f.link)?'<a href="'+esc(f.link)+'" target="_blank" rel="noopener noreferrer">'+esc(f.text)+'</a>':esc(f.text);
    const dates=[f.as_of?'as of '+esc(f.as_of):'',f.retrieved?'retrieved '+esc(f.retrieved):''].filter(Boolean).join(' · ');
    const src=safe(f.src)?'<a class="check" href="'+esc(f.src)+'" target="_blank" rel="noopener noreferrer">Check the evidence ↗</a>':'no source';
    return '<span class="'+(f.missing?'missing':'')+'">'+val+'</span>'+(f.reason?'<span class="reason">'+esc(f.reason)+'</span>':'')+'<span class="src">'+src+(dates?' · '+dates:'')+'</span>'}
  function render(){
    if(orgs.length<2){cmp.innerHTML='<p class="empty">Choose two or three companies above for a side-by-side view, or use the table below.</p>';return}
    const data=orgs.map(o=>loaded[o]);if(data.some(d=>!d)){cmp.innerHTML='<p class="empty">Loading…</p>';return}
    const keys=[];data.forEach(d=>d.facts.forEach(f=>{if(!keys.some(k=>k[0]===f.key))keys.push([f.key,f.label])}));
    let out='<div class="cmp" style="--n:'+data.length+'"><div class="r h-row"><div class="l">Company</div>'+data.map(d=>'<div class="h"><a href="companies/'+esc(d.org)+'.html">'+esc(d.name)+'</a></div>').join('')+'</div>';
    keys.forEach(([k,label])=>{out+='<div class="r"><div class="l">'+esc(label.replace(/ \(FY\d{4}\)$/,''))+'</div>'+data.map(d=>{const f=d.facts.find(x=>x.key===k);
      return '<div data-company="'+esc(d.name)+'">'+(f&&/\(FY\d{4}\)$/.test(f.label)?'<span class="src">'+esc(f.label.match(/FY\d{4}/)[0])+'</span>':'')+cell(f)+'</div>'}).join('')+'</div>'});
    cmp.innerHTML=out+'</div>';
  }
  load();
}
})();
