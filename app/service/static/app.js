"use strict";
let pending=false;
const button=document.getElementById("refresh");
const text=(id,value)=>{document.getElementById(id).textContent=String(value??"—");};
async function refresh(){
 if(pending||document.hidden)return;
 pending=true;button.disabled=true;
 const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),12000);
 try{
  const response=await fetch("/api/status",{cache:"no-store",signal:abort.signal});
  const value=await response.json();
  if(value.app){text("app",value.app);document.title=value.app+" Indexer";}
  text("network",value.network);
  if(!response.ok)throw new Error("Waiting for a compatible index and verified node. Check whether indexing is enabled.");
  for(const id of ["nodeHeight","indexHeight","items"])text(id,value[id]);
  text("message",value.observedSynchronized?"Your index matches the verified node tip.":"Your index is catching up.");
 }catch(error){text("message",error.name==="AbortError"?"Status request timed out. Try again.":error.message);for(const id of ["nodeHeight","indexHeight","items"])text(id,"Unavailable");}
 finally{clearTimeout(timer);pending=false;button.disabled=false;}
}
button.addEventListener("click",refresh);document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});setInterval(refresh,15000);refresh();
