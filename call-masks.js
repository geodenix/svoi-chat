/* Video masks for the server-admin account. */
(()=>{
'use strict';
const modes=[
{id:'none',label:'Без маски',emoji:'🚫'},
{id:'glasses',label:'Очки',emoji:'🕶️'},
{id:'cat',label:'Кот',emoji:'🐱'},
{id:'crown',label:'Корона',emoji:'👑'},
{id:'robot',label:'Робот',emoji:'🤖'}
];
let mode='none',detectorPromise;
function faceDetector(){
 if(!detectorPromise)detectorPromise=(async()=>{
  const {FaceDetector,FilesetResolver}=await import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21/+esm');
  const vision=await FilesetResolver.forVisionTasks('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21/wasm');
  return FaceDetector.createFromOptions(vision,{baseOptions:{
   modelAssetPath:'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite',
   delegate:'CPU'
  },runningMode:'VIDEO',minDetectionConfidence:.55});
 })().catch(error=>{detectorPromise=null;throw error});
 return detectorPromise;
}
function poly(ctx,pts,fill){
 ctx.beginPath();ctx.moveTo(pts[0][0],pts[0][1]);
 pts.slice(1).forEach(p=>ctx.lineTo(p[0],p[1]));ctx.closePath();
 ctx.fillStyle=fill;ctx.fill();
}
function drawMask(ctx,b){
 const {x,y,w,h}=b;
 if(mode==='glasses'){
  ctx.lineWidth=Math.max(2,w*.02);ctx.strokeStyle='#dbe8f8';ctx.fillStyle='#0c1729';
  for(const cx of [.35,.65]){ctx.beginPath();ctx.ellipse(x+w*cx,y+h*.42,w*.16,h*.105,0,0,7);ctx.fill();ctx.stroke()}
  ctx.beginPath();ctx.moveTo(x+w*.48,y+h*.42);ctx.lineTo(x+w*.52,y+h*.42);ctx.stroke();
 }else if(mode==='cat'){
  poly(ctx,[[x+w*.12,y+h*.10],[x+w*.10,y-h*.27],[x+w*.43,y+h*.02]],'#ffc3d1');
  poly(ctx,[[x+w*.57,y+h*.02],[x+w*.90,y-h*.27],[x+w*.88,y+h*.10]],'#ffc3d1');
  poly(ctx,[[x+w*.44,y+h*.65],[x+w*.56,y+h*.65],[x+w*.5,y+h*.72]],'#fa85a6');
  ctx.strokeStyle='#fcecf4';ctx.lineWidth=Math.max(2,w*.014);
  [-1,1].forEach(s=>[-1,0,1].forEach(n=>{ctx.beginPath();
   ctx.moveTo(x+w*(.5+s*.07),y+h*.71);ctx.lineTo(x+w*(.5+s*.40),y+h*(.71+n*.07));ctx.stroke()}));
 }else if(mode==='crown'){
  poly(ctx,[[x+w*.16,y-h*.01],[x+w*.14,y-h*.34],[x+w*.36,y-h*.18],[x+w*.5,y-h*.43],[x+w*.64,y-h*.18],[x+w*.86,y-h*.34],[x+w*.84,y-h*.01]],'#ffd24b');
  ctx.fillStyle='#3a9dff';ctx.beginPath();ctx.arc(x+w*.5,y-h*.08,w*.04,0,7);ctx.fill();
 }else if(mode==='robot'){
  ctx.fillStyle='rgba(4,31,63,.85)';ctx.strokeStyle='#42eaff';ctx.lineWidth=Math.max(3,w*.025);
  ctx.fillRect(x+w*.12,y+h*.29,w*.76,h*.28);
  ctx.strokeRect(x+w*.12,y+h*.29,w*.76,h*.28);
  ctx.fillStyle='#52dfff';ctx.fillRect(x+w*.23,y+h*.40,w*.54,h*.05);
 }
}
class MaskPipeline{
 constructor(){this.video=null;this.canvas=null;this.track=null;this.detector=null;this.bounds=null;this.frame=0;this.active=false;this.lastDetection=0}
 async start(source){
  if(!source||source.readyState!=='live')throw Error('Камера не включена');
  if(!HTMLCanvasElement.prototype.captureStream)throw Error('Браузер не поддерживает маски');
  const video=document.createElement('video');
  video.autoplay=true;video.muted=true;video.playsInline=true;
  video.srcObject=new MediaStream([source]);this.video=video;
  try{await video.play()}catch(error){this.stop();throw error}
  const settings=source.getSettings?.()||{};
  const w=video.videoWidth||settings.width||640,h=video.videoHeight||settings.height||480;
  const scale=Math.min(1,960/Math.max(w,h));
  const canvas=document.createElement('canvas');
  canvas.width=Math.max(2,Math.round(w*scale/2)*2);
  canvas.height=Math.max(2,Math.round(h*scale/2)*2);
  const ctx=canvas.getContext('2d',{alpha:false});
  if(!ctx){this.stop();throw Error('Canvas недоступен')}
  this.canvas=canvas;
  ctx.fillStyle='#101b2d';ctx.fillRect(0,0,canvas.width,canvas.height);
  this.track=canvas.captureStream(24).getVideoTracks()[0];
  if(!this.track){this.stop();throw Error('Не удалось создать маску')}
  this.active=true;
  faceDetector().then(d=>{if(this.active)this.detector=d}).catch(e=>{
   if(this.active)document.dispatchEvent(new CustomEvent('svoi-mask-error',{detail:'Не удалось загрузить распознавание лица: '+(e.message||'проверьте интернет')}));
  });
  const draw=stamp=>{
   if(!this.active)return;
   if(video.readyState>=2){
    ctx.drawImage(video,0,0,canvas.width,canvas.height);
    if(mode!=='none'&&this.detector){
     if(stamp-this.lastDetection>=200){
      this.lastDetection=stamp;
      try{
       const b=this.detector.detectForVideo(video,stamp).detections?.[0]?.boundingBox;
       if(b){
        const sw=canvas.width/(video.videoWidth||w),sh=canvas.height/(video.videoHeight||h);
        const next={x:b.originX*sw,y:b.originY*sh,w:b.width*sw,h:b.height*sh};
        if(this.bounds)for(const k of ['x','y','w','h'])next[k]=this.bounds[k]*.55+next[k]*.45;
        this.bounds=next;
       }else this.bounds=null;
      }catch{this.bounds=null}
     }
     if(this.bounds)drawMask(ctx,this.bounds);
    }
   }
   this.frame=requestAnimationFrame(draw);
  };
  this.frame=requestAnimationFrame(draw);
  return this.track;
 }
 stop(){
  this.active=false;
  if(this.frame)cancelAnimationFrame(this.frame);
  this.frame=0;this.bounds=null;this.detector=null;
  this.track?.stop();this.track=null;
  if(this.video){this.video.pause();this.video.srcObject=null;this.video=null}
  this.canvas=null;
 }
}
function createProcessor(){
 return {
  name:'svoi-admin-mask',processedTrack:null,pipeline:null,
  async init(options){
   this.pipeline=new MaskPipeline();
   this.processedTrack=await this.pipeline.start(options.track)
  },
  async restart(options){await this.destroy();await this.init(options)},
  async destroy(){this.pipeline?.stop();this.pipeline=null;this.processedTrack=null}
 };
}
window.SvoiCallMasks={
 modes,getMode:()=>mode,
 setMode:id=>{mode=modes.some(x=>x.id===id)?id:'none'},
 createPipeline:()=>new MaskPipeline(),
 createProcessor
};
})();