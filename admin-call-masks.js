/* Integrates the admin-only masks with WebRTC and LiveKit. */
(()=>{
'use strict';
let privateMask=null,privateSerial=Promise.resolve(),privateVersion=0;
let groupTrack=null,groupProcessor=null,groupSerial=Promise.resolve();
const engine=()=>window.SvoiCallMasks;
const choice=()=>me?.is_server_admin?engine()?.getMode():'none';
function notice(message){
 const el=document.getElementById('adminMaskNote');
 if(el)el.textContent=message;
}
function init(){
 const authorized=!!me?.is_server_admin;
 const container=document.getElementById('adminMaskChoices');
 if(!container||!engine())return;
 const saved=authorized?localStorage.getItem('svoi_admin_mask_'+me.id):'none';
 engine().setMode(saved||'none');
 container.replaceChildren();
 document.getElementById('adminMaskSection')?.classList.toggle('hidden',!authorized);
 if(!authorized)return;
 for(const item of engine().modes){
  const b=document.createElement('button');
  b.type='button';b.className='admin-mask-choice';
  b.setAttribute('aria-pressed',String(choice()===item.id));
  b.innerHTML='<span aria-hidden="true"></span><small></small>';
  b.querySelector('span').textContent=item.emoji;
  b.querySelector('small').textContent=item.label;
  b.classList.toggle('active',choice()===item.id);
  b.onclick=()=>{
   engine().setMode(item.id);
   localStorage.setItem('svoi_admin_mask_'+me.id,item.id);
   for(const child of container.children){
    const selected=child===b;
    child.classList.toggle('active',selected);
    child.setAttribute('aria-pressed',String(selected));
   }
   notice(item.id==='none'?'Маски выключены.':'Маска «'+item.label+'» выбрана для вашей камеры.');
   schedulePrivate();scheduleGroup();
  };
  container.append(b);
 }
}
function schedulePrivate(){
 const version=++privateVersion;
 privateSerial=privateSerial.then(()=>syncPrivate(version)).catch(error=>{
  notice('Маска не включилась: '+(error.message||'ошибка камеры'));
 });
}
async function syncPrivate(version){
 const call=currentCall,raw=localVideoTrack();
 const sender=call?.pc?.getSenders?.().find(s=>s.track?.kind==='video');
 const enabled=choice()!=='none'&&!!raw?.enabled&&!!sender&&!call?.cameraOff;
 const old=privateMask;
 if(old&&enabled&&old.call===call&&old.source===raw&&sender.track===old.pipeline.track)return;
 privateMask=null;
 if(old){
  if(sender?.track===old.pipeline.track&&raw?.readyState==='live'){
   try{await sender.replaceTrack(raw)}catch{}
  }
  old.pipeline.stop();
  if(call===currentCall)restorePrivateLocalPreview(call);
 }
 if(!enabled||version!==privateVersion)return;
 const pipeline=engine().createPipeline();
 try{
  await pipeline.start(raw);
  if(version!==privateVersion||currentCall!==call||localVideoTrack()!==raw){pipeline.stop();return}
  await sender.replaceTrack(pipeline.track);
  if(version!==privateVersion||currentCall!==call){pipeline.stop();return}
  privateMask={call,source:raw,pipeline};
  const preview=document.getElementById('localVideo');
  if(preview){preview.srcObject=new MediaStream([pipeline.track]);preview.play().catch(()=>{})}
 }catch(error){pipeline.stop();throw error}
}
function stopPrivate(){
 privateVersion++;
 const old=privateMask;privateMask=null;
 old?.pipeline.stop();
}
function scheduleGroup(){
 groupSerial=groupSerial.then(syncGroup).catch(error=>{
  notice('Маска группового звонка: '+(error.message||'не удалось включить'));
 });
}
async function syncGroup(){
 const state=groupCallState;
 const participant=state?.room?.localParticipant;
 const pub=participant?[...participant.videoTrackPublications.values()].find(p=>p.track):null;
 const next=(!state?.cameraOff&&choice()!=='none')?pub?.track:null;
 if(groupTrack&&groupTrack!==next){
  const oldTrack=groupTrack,processor=groupProcessor;
  groupTrack=null;groupProcessor=null;
  try{
   if(oldTrack.getProcessor?.()===processor)await oldTrack.stopProcessor();
   else await processor?.destroy();
  }catch{try{await processor?.destroy()}catch{}}
 }
 if(!next||groupTrack===next)return;
 const processor=engine().createProcessor();
 try{
  await next.setProcessor(processor,true);
  if(groupCallState!==state||state.cameraOff||choice()==='none'){
   try{await next.stopProcessor()}catch{await processor.destroy()}
   return;
  }
  groupTrack=next;groupProcessor=processor;
 }catch(error){await processor.destroy();throw error}
}
document.addEventListener('svoi-mask-error',event=>{
 if(me?.is_server_admin)notice(event.detail||'Распознавание лица недоступно');
});
window.SvoiAdminMasks={init,schedulePrivate,scheduleGroup,stopPrivate};
})();
