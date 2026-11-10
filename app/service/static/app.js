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
  if(!response.ok)throw new Error(value.indexingEnabled === false ? "Indexing paused. Enable indexing in this app’s Umbrel settings when ready." : "Waiting for a compatible index and verified node.");
  for(const id of ["nodeHeight","indexHeight","items"])text(id,value[id]);
  text("message",value.indexingEnabled === false ? `Indexing paused at block ${value.indexHeight}.` : value.observedSynchronized ? `Your index matches the verified node at block ${value.indexHeight}.` : `Indexing block ${value.indexHeight} of ${value.nodeHeight}.`);
 }catch(error){text("message",error.name==="AbortError"?"Status request timed out. Try again.":error.message);for(const id of ["nodeHeight","indexHeight","items"])text(id,"Unavailable");}
 finally{clearTimeout(timer);pending=false;button.disabled=false;}
}

new DashboardRefresh(refresh);
