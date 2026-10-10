const $=id=>document.getElementById(id);
let mode='login',token=localStorage.getItem('svoi_token')||'',me=null,users=[],groups=[],callHistory=[],active=null,socket=null,retry=null,pendingAttachment=null,uploading=false,foundUser=null;
let wsHasConnected=false,wsReconnectSyncTimer=null;
let chatBackgroundRequest=0,currentChatBackgroundUrl='';
let currentMessages=[],typingStopTimer=null,lastTypingSentAt=0;
let privateChatLoadRequest=0,privateUsersRefreshTimer=null;
const MESSAGE_PAGE_SIZE=50;
let messageHistoryLoading=false,messageHistoryHasMore=false,messageHistoryKey='',messageHistoryRequest=0,messageHistoryReadyAt=0;
let forwardSource=null,replySource=null,editSource=null,messageActionSource=null;
const incomingTyping=new Map();
let messageRecorder=null,messageRecordStream=null,messageRecordChunks=[],messageRecordKind=null,messageRecordTimer=null,messageRecordStartedAt=0,messageRecordBlob=null,messageRecordMime='',messageRecordTarget=null,messageRecordAction=null,messageRecordObjectUrl='';
let messageRecordFacing='user';
let messageMicStream=null,messageCameraStream=null,messageRecordCanvas=null,messageRecordCanvasRaf=null;
let recordingAudioContext=null,recordingAnalyser=null,recordingWaveRaf=null;
let currentCall=null,pendingCall=null,pendingIce=[],cameraFacing='user',acceptingCall=false,preparedMediaStream=null,incomingMediaRequest=null,earModeActive=false,earUnlockTimer=null;
let callSignalChain=Promise.resolve();
let callAudioSinkId='',callSpeakerMode=false;
let audioOutputSwitching=false,nativeAudioRouteChain=Promise.resolve(),audioRouteGeneration=0;
let soundsEnabled=localStorage.getItem('svoi_sounds')!=='0',vibrationEnabled=localStorage.getItem('svoi_vibration')!=='0',audioContext=null,ringtoneTimer=null,outgoingToneTimer=null;
let deferredInstallPrompt=null;
let adminRefreshTimer=null;
let adminStatsPeriod=localStorage.getItem('svoi_admin_stats_period')==='week'?'week':'day';
let groupCallState=null;
let minimizedCallKind=null;
let groupMembersData=null;
let groupCallProfileUser=null;
let addParticipantContext=null;
let chatGalleryState={
  kind:'photos',
  items:[],
  nextBeforeId:null,
  loading:false,
  requestId:0,
  targetKey:''
};
const outboxDeliveries=new Map();
const pendingGroupReceiptSummaries=new Map();
let outboxFlushTimer=null;
let outboxFlushRunning=false;
let outboxRetryStep=0;
const OUTBOX_RETRY_DELAYS=[1500,4000,10000,30000,60000];
let clientDiagnosticsStarted=false;
let clientDiagnosticStaticMetaPromise=null;
const clientErrorSeen=new Map();
let privateParticipantSize=Math.max(0,Math.min(2,Number(localStorage.getItem('svoi_private_video_size')||1)));
let privateLocalVideoPosition=(()=>{
  try{
    const saved=JSON.parse(localStorage.getItem('svoi_private_pip_position')||'null');
    if(saved&&Number.isFinite(saved.x)&&Number.isFinite(saved.y)){
      return {
        x:Math.max(0,Math.min(1,saved.x)),
        y:Math.max(0,Math.min(1,saved.y))
      }
    }
  }catch{}
  return {x:1,y:0}
})();

function setDrawerButton(button,icon,label,value){
  if(!button)return;
  button.replaceChildren();
  const left=document.createElement('span');left.textContent=icon+' '+label;
  const right=document.createElement('span');right.textContent=value;
  button.append(left,right)
}

function updateSoundButton(){
  const button=$('soundBtn');
  if(!button)return;
  setDrawerButton(button,soundsEnabled?'🔊':'🔇','Звуки',soundsEnabled?'Вкл':'Выкл');
  button.title=soundsEnabled?'Звуки включены':'Звуки выключены'
}

function nativeVibrationPlugin(){
  return window.Capacitor?.Plugins?.NativeVibration||null
}

function vibrationSupported(){
  return !!nativeVibrationPlugin()||typeof navigator.vibrate==='function'
}

function webVibrate(pattern){
  if(typeof navigator.vibrate!=='function')return false;
  try{return navigator.vibrate(pattern)!==false}catch{return false}
}

function performVibration(pattern){
  if(!vibrationEnabled)return;
  const normalized=Array.isArray(pattern)?pattern:[pattern];
  const plugin=nativeVibrationPlugin();
  if(plugin){
    plugin.vibrate({pattern:normalized}).catch(()=>{
      webVibrate(pattern)
    });
    return
  }
  webVibrate(pattern)
}

function stopVibration(){
  const plugin=nativeVibrationPlugin();
  if(plugin){
    plugin.cancel().catch(()=>{})
  }
  if(typeof navigator.vibrate==='function'){
    try{navigator.vibrate(0)}catch{}
  }
}

function updateVibrationButton(){
  const button=$('vibrationBtn');
  if(!button)return;
  const supported=vibrationSupported();
  setDrawerButton(
    button,
    !supported?'—':(vibrationEnabled?'📳':'📴'),
    'Вибрация',
    !supported?'Нет':(vibrationEnabled?'Вкл':'Выкл')
  );
  button.title=!supported
    ?'Вибрация недоступна на этом устройстве'
    :(vibrationEnabled?'Вибрация включена':'Вибрация выключена');
  button.disabled=!supported
}

async function unlockAudio(){
  if(!soundsEnabled)return null;
  const AudioCtx=window.AudioContext||window.webkitAudioContext;
  if(!AudioCtx)return null;
  if(!audioContext)audioContext=new AudioCtx();
  if(audioContext.state==='suspended'){
    try{await audioContext.resume()}catch{}
  }
  return audioContext
}

function tone(frequency,when,duration=0.11,volume=0.04,type='sine'){
  if(!audioContext||audioContext.state!=='running')return;
  const oscillator=audioContext.createOscillator();
  const gain=audioContext.createGain();
  oscillator.type=type;
  oscillator.frequency.setValueAtTime(frequency,when);
  gain.gain.setValueAtTime(0.0001,when);
  gain.gain.exponentialRampToValueAtTime(Math.max(0.0002,volume),when+0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001,when+duration);
  oscillator.connect(gain);
  gain.connect(audioContext.destination);
  oscillator.start(when);
  oscillator.stop(when+duration+0.03)
}

async function playMessageSound(){
  if(!soundsEnabled)return;
  const ctx=await unlockAudio();
  if(!ctx||ctx.state!=='running')return;
  const t=ctx.currentTime+0.01;
  tone(740,t,0.08,0.035,'sine');
  tone(980,t+0.10,0.12,0.045,'sine')
}

async function playRingPulse(){
  if(!soundsEnabled)return;
  const ctx=await unlockAudio();
  if(!ctx||ctx.state!=='running')return;
  const t=ctx.currentTime+0.01;

  // Original bright digital ringtone with a light rising/falling motif.
  // Similar feel to classic VoIP ringtones, but not a copy of Skype's tune.
  const notes=[
    [659,0.00,0.13],
    [784,0.14,0.13],
    [988,0.28,0.16],
    [880,0.47,0.13],
    [740,0.62,0.13],
    [988,0.80,0.18],
    [784,1.03,0.14],
    [659,1.20,0.20]
  ];

  for(const [freq,offset,duration] of notes){
    tone(freq,t+offset,duration,0.045,'sine');
    tone(freq*2,t+offset,duration*0.75,0.010,'triangle')
  }
}

async function playOutgoingTonePulse(){
  if(!soundsEnabled)return;
  const ctx=await unlockAudio();
  if(!ctx||ctx.state!=='running')return;
  const t=ctx.currentTime+0.01;

  // Soft original waiting melody for the caller.
  tone(523,t,0.18,0.022,'sine');
  tone(659,t+0.20,0.18,0.020,'sine');
  tone(784,t+0.42,0.24,0.018,'sine');
  tone(659,t+0.70,0.16,0.014,'sine')
}

async function startOutgoingTone(){
  if(!soundsEnabled||outgoingToneTimer)return;
  await playOutgoingTonePulse();
  outgoingToneTimer=setInterval(()=>{
    playOutgoingTonePulse().catch(()=>{})
  },2400)
}

function stopOutgoingTone(){
  if(outgoingToneTimer){
    clearInterval(outgoingToneTimer);
    outgoingToneTimer=null
  }
}

function vibrateMessage(){
  performVibration(90)
}

function vibrateIncomingCall(){
  performVibration([650,220,650,900])
}

function vibrateGroupInvite(){
  performVibration([180,120,260])
}

async function startRingtone(){
  if((!soundsEnabled&&!vibrationEnabled)||ringtoneTimer)return;
  if(soundsEnabled)await playRingPulse();
  vibrateIncomingCall();
  ringtoneTimer=setInterval(()=>{
    if(soundsEnabled)playRingPulse().catch(()=>{});
    vibrateIncomingCall()
  },3000)
}

function stopRingtone(){
  if(ringtoneTimer){
    clearInterval(ringtoneTimer);
    ringtoneTimer=null
  }
  stopVibration()
}

document.addEventListener('pointerdown',()=>{unlockAudio().catch(()=>{})},{passive:true});
document.addEventListener('keydown',()=>{unlockAudio().catch(()=>{})},{passive:true});

function isStandalone(){
  return window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone===true
}

function isAndroidDevice(){
  return /Android/i.test(navigator.userAgent||'')
}

function isIosDevice(){
  const ua=navigator.userAgent||'';
  return /iPhone|iPad|iPod/i.test(ua)
    || (navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1)
}

function isNativeMobileApp(){
  try{
    return !!window.Capacitor?.isNativePlatform?.()
  }catch{
    return false
  }
}

const latestAndroidApkUrl='https://github.com/geodenix/svoi-chat/releases/latest/download/svoi.apk';

function updateInstallUi(){
  const box=$('installBox');
  const button=$('installBtn');
  if(!box||!button)return;

  const hide=isStandalone()||isNativeMobileApp();
  box.classList.toggle('hidden',hide);
  if(hide)return;

  const label=button.querySelector('span');
  if(label){
    if(isAndroidDevice())label.textContent='📲 Скачать приложение «Свои»';
    else if(isIosDevice())label.textContent='📲 Добавить «Свои» на экран';
    else label.textContent='📲 Установить «Свои»'
  }
}

window.addEventListener('beforeinstallprompt',event=>{
  event.preventDefault();
  deferredInstallPrompt=event;
  updateInstallUi()
});

window.addEventListener('appinstalled',()=>{
  deferredInstallPrompt=null;
  $('installBox').classList.add('hidden')
});

$('installBtn').onclick=async()=>{
  if(isStandalone()||isNativeMobileApp()){
    $('installBox').classList.add('hidden');
    return
  }

  if(isAndroidDevice()){
    const link=document.createElement('a');
    link.href=latestAndroidApkUrl;
    link.rel='noopener';
    document.body.append(link);
    link.click();
    link.remove();
    return
  }

  if(isIosDevice()){
    alert('На iPhone открой меню «Поделиться» в Safari и выбери «На экран Домой». После этого «Свои» будет запускаться как отдельное приложение.')
    return
  }

  if(deferredInstallPrompt){
    deferredInstallPrompt.prompt();
    try{await deferredInstallPrompt.userChoice}catch{}
    deferredInstallPrompt=null;
    updateInstallUi();
    return
  }

  alert('Открой меню браузера и выбери «Добавить на главный экран» или «Установить приложение».')
};

updateInstallUi();
updateSoundButton();
updateVibrationButton();
applyPrivateParticipantSize();
applyGroupAutoLayout();

$('soundBtn').onclick=async()=>{
  soundsEnabled=!soundsEnabled;
  localStorage.setItem('svoi_sounds',soundsEnabled?'1':'0');
  if(!soundsEnabled){
    stopOutgoingTone();
    if(!vibrationEnabled)stopRingtone()
  }else{
    await unlockAudio();
    playMessageSound().catch(()=>{})
  }
  updateSoundButton()
};

$('vibrationBtn').onclick=()=>{
  if(!vibrationSupported())return;
  vibrationEnabled=!vibrationEnabled;
  localStorage.setItem('svoi_vibration',vibrationEnabled?'1':'0');
  if(vibrationEnabled){
    performVibration([90,70,140])
  }else{
    stopVibration()
  }
  updateVibrationButton()
};

let pendingNotificationOpen=null;

async function openNotificationTarget(action){
  const url=new URL(String(action?.url||'/'),location.origin);
  if(url.origin!==location.origin)return;
  if(!me){pendingNotificationOpen=action;return}
  const callId=url.searchParams.get('incoming_call');
  if(callId){
    if(groupCallState||(currentCall&&String(currentCall.callId)!==callId))return;
    window.handleNativeCallAction(url.href);
    return
  }
  const groupId=Number(url.searchParams.get('group_call'));
  if(groupId){
    if(currentCall||pendingCall||groupCallState)return;
    await joinGroupCall(groupId,url.searchParams.get('video')==='1',false);
    return
  }
  const tag=String(action?.tag||url.searchParams.get('notification_chat')||'');
  const match=/^(user|group)-([1-9]\d*)$/.exec(tag);
  if(!match)return;
  const id=Number(match[2]);
  if(!Number.isSafeInteger(id))return;
  const isUser=match[1]==='user';
  let target=(isUser?users:groups).find(item=>Number(item.id)===id);
  if(!target){
    await (isUser?loadUsers():loadGroups());
    target=(isUser?users:groups).find(item=>Number(item.id)===id)
  }
  if(!target)return;
  if(currentCall)minimizePrivateCall();
  if(groupCallState)minimizeGroupCall();
  await (isUser?openUser(target):openGroup(target))
}

function resumeNotificationOpen(){
  const url=new URL(location.href);
  const tag=url.searchParams.get('notification_chat');
  const action=pendingNotificationOpen||(tag?{url:url.href,tag}:null);
  if(!action)return;
  pendingNotificationOpen=null;
  if(tag){
    url.searchParams.delete('notification_chat');
    history.replaceState({},'',url.pathname+url.search+url.hash)
  }
  openNotificationTarget(action).catch(err=>console.warn('notification open failed',err))
}

if('serviceWorker' in navigator){
  navigator.serviceWorker.addEventListener('message',event=>{
    if(event.data?.type!=='svoi_notification_open')return;
    openNotificationTarget(event.data).catch(err=>console.warn('notification open failed',err))
  });
  navigator.serviceWorker.register('/sw.js')
    .then(registration=>registration.update().catch(()=>{}))
    .catch(()=>{})
}

function currentSessionDeviceLabel(){
  const ua=navigator.userAgent||'';
  let platform='Web';
  if(/Android/i.test(ua))platform='Android';
  else if(/iPhone|iPad|iPod/i.test(ua))platform='iPhone';
  else if(/Windows/i.test(ua))platform='Windows';
  else if(/Macintosh|Mac OS X/i.test(ua))platform='macOS';
  let mode='browser';
  try{
    if(window.Capacitor?.isNativePlatform?.())mode='app';
    else if(window.matchMedia('(display-mode: standalone)').matches)mode='pwa'
  }catch{}
  return platform+'|'+mode
}

const authHeaders=()=>token?{Authorization:'Bearer '+token}:{};

const api=async(path,opt={})=>{
  opt.headers={...(opt.headers||{}),'X-Svoi-Device':currentSessionDeviceLabel(),...authHeaders()};
  if(opt.body&&typeof opt.body!=='string'){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(opt.body)}
  const r=await fetch(path,opt);let data=null;try{data=await r.json()}catch{}
  if(!r.ok){const err=new Error(data?.detail||'Ошибка сервера');err.status=r.status;throw err}
  return data
};

function scrubDiagnosticText(value,maxLength=1200){
  let text=String(value??'');
  text=text.replace(
    /([?&](?:token|access_token|auth|authorization|code)=)[^&#\\s)]+/gi,
    '$1[redacted]'
  );
  return text.slice(0,maxLength)
}

function diagnosticSourcePath(value){
  if(!value)return null;
  try{
    const url=new URL(String(value),location.href);
    if(url.origin===location.origin)return url.pathname.slice(0,500);
    return (url.hostname+url.pathname).slice(0,500)
  }catch{
    return scrubDiagnosticText(String(value).split('?')[0],500)
  }
}

function diagnosticHash(value){
  const text=String(value||'');
  let hash=2166136261;
  for(let i=0;i<text.length;i++){
    hash^=text.charCodeAt(i);
    hash=Math.imul(hash,16777619)
  }
  return (hash>>>0).toString(16).padStart(8,'0')
}

function diagnosticClientType(){
  if(isNativeMobileApp()){
    const platform=window.Capacitor?.getPlatform?.()||'native';
    return platform==='android'?'Android app':platform+' app'
  }
  if(isAndroidDevice())return isStandalone()?'Android PWA':'Android web';
  if(isIosDevice())return isStandalone()?'iOS PWA':'iOS web';
  return 'Web'
}

async function clientDiagnosticStaticMeta(){
  if(clientDiagnosticStaticMetaPromise)return clientDiagnosticStaticMetaPromise;
  clientDiagnosticStaticMetaPromise=(async()=>{
    const result={
      client_type:diagnosticClientType(),
      app_version:null,
      app_version_code:null
    };
    if(isNativeMobileApp()){
      try{
        const plugin=window.Capacitor?.Plugins?.NativeAppInfo;
        if(plugin?.getInfo){
          const info=await plugin.getInfo();
          if(info?.versionName)result.app_version=String(info.versionName).slice(0,64);
          if(Number.isFinite(Number(info?.versionCode))){
            result.app_version_code=Number(info.versionCode)
          }
        }
      }catch{}
    }
    return result
  })();
  return clientDiagnosticStaticMetaPromise
}

function clientDiagnosticContext(){
  const values=[
    'visibility='+String(document.visibilityState||'unknown'),
    'screen='+(active?.type||'none'),
    'call='+(groupCallState?'group':((currentCall||pendingCall)?'private':'none'))
  ];
  return values.join(' · ').slice(0,1000)
}

async function reportClientError(kind,message,extra={}){
  if(!me)return;
  const safeMessage=scrubDiagnosticText(
    message||extra?.stack||'Неизвестная ошибка',
    1200
  );
  if(!safeMessage)return;

  const source=diagnosticSourcePath(extra?.source);
  const fingerprint=diagnosticHash(
    [kind,safeMessage,source||'',extra?.line_no||0,extra?.column_no||0].join('|')
  );
  const now=Date.now();
  const previous=clientErrorSeen.get(fingerprint)||0;
  if(now-previous<60000)return;
  clientErrorSeen.set(fingerprint,now);
  if(clientErrorSeen.size>120){
    for(const [key,time] of clientErrorSeen){
      if(now-time>300000)clientErrorSeen.delete(key)
    }
  }

  const staticMeta=await clientDiagnosticStaticMeta();
  const connection=navigator.connection
    ||navigator.mozConnection
    ||navigator.webkitConnection
    ||null;
  const downlink=Number(connection?.downlink);
  const rtt=Number(connection?.rtt);

  const payload={
    kind:['js_error','promise_rejection','manual'].includes(kind)?kind:'manual',
    message:safeMessage,
    source,
    line_no:Number.isFinite(Number(extra?.line_no))?Number(extra.line_no):null,
    column_no:Number.isFinite(Number(extra?.column_no))?Number(extra.column_no):null,
    stack:scrubDiagnosticText(extra?.stack||'',8000)||null,
    page_path:String(location.pathname||'/').slice(0,500),
    client_type:staticMeta.client_type,
    app_version:staticMeta.app_version,
    app_version_code:staticMeta.app_version_code,
    user_agent:String(navigator.userAgent||'').slice(0,1200)||null,
    network_type:String(connection?.type||'').slice(0,64)||null,
    effective_type:String(connection?.effectiveType||'').slice(0,32)||null,
    downlink_mbps:Number.isFinite(downlink)&&downlink>=0?downlink:null,
    network_rtt_ms:Number.isFinite(rtt)&&rtt>=0?rtt:null,
    online:navigator.onLine!==false,
    context:clientDiagnosticContext(),
    fingerprint
  };

  try{
    await fetch('/api/client-errors',{
      method:'POST',
      headers:{
        'Content-Type':'application/json',
        ...authHeaders()
      },
      body:JSON.stringify(payload),
      keepalive:true
    })
  }catch{}
}

function startClientDiagnostics(){
  if(clientDiagnosticsStarted)return;
  clientDiagnosticsStarted=true;

  window.addEventListener('error',event=>{
    const message=event?.message||event?.error?.message;
    if(!message)return;
    reportClientError('js_error',message,{
      source:event?.filename||null,
      line_no:event?.lineno,
      column_no:event?.colno,
      stack:event?.error?.stack||''
    }).catch(()=>{})
  });

  window.addEventListener('unhandledrejection',event=>{
    const reason=event?.reason;
    const message=reason?.message
      ||(typeof reason==='string'?reason:'Необработанная ошибка Promise');
    reportClientError('promise_rejection',message,{
      stack:reason?.stack||''
    }).catch(()=>{})
  })
}

function makeClientMessageId(){
  if(globalThis.crypto?.randomUUID)return crypto.randomUUID();
  return Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)+'-'+Math.random().toString(36).slice(2)
}

function outboxKey(){
  return me?'svoi_outbox_'+me.id:''
}

function readOutbox(){
  const key=outboxKey();if(!key)return [];
  try{
    const value=JSON.parse(localStorage.getItem(key)||'[]');
    return Array.isArray(value)?value.filter(item=>item&&item.id&&item.path):[]
  }catch{return []}
}

function writeOutbox(items){
  const key=outboxKey();if(!key)return;
  if(items.length)localStorage.setItem(key,JSON.stringify(items));
  else localStorage.removeItem(key)
}

function updateOutboxEntry(id,patch={}){
  const items=readOutbox();
  const index=items.findIndex(item=>item.id===id);
  if(index<0)return null;
  const updated={...items[index],...patch};
  items[index]=updated;
  writeOutbox(items);
  return updated
}

function queueOutbox(entry){
  const items=readOutbox();
  const index=items.findIndex(item=>item.id===entry.id);
  if(index<0){
    const queued={
      ...entry,
      state:entry.state||'waiting',
      attempts:Number(entry.attempts)||0
    };
    items.push(queued);
    writeOutbox(items);
    return queued
  }
  return items[index]
}

function removeOutbox(id){
  writeOutbox(readOutbox().filter(item=>item.id!==id))
}

function outboxLocalMessageId(id){
  const text=String(id||'');
  let hash=2166136261;
  for(let i=0;i<text.length;i++){
    hash^=text.charCodeAt(i);
    hash=Math.imul(hash,16777619)
  }
  return -1-((hash>>>0)%2000000000)
}

function outboxMessage(entry,state=null,error=''){
  const payload=entry?.payload||{};
  const attachment=entry?.attachment||null;
  const body=String(
    payload.body
    ||(!attachment&&payload.attachment_id?'📎 Вложение':'')
    ||''
  );
  return {
    id:outboxLocalMessageId(entry.id),
    client_message_id:entry.id,
    sender_id:Number(me?.id)||0,
    recipient_id:entry.chat_type==='user'?Number(entry.chat_id):null,
    group_id:entry.chat_type==='group'?Number(entry.chat_id):null,
    sender_name:me?.display_name||'Я',
    body,
    created_at:entry.queued_at||new Date().toISOString(),
    edited_at:null,
    delivered_at:null,
    read_at:null,
    forwarded:false,
    reply_to_message_id:payload.reply_to_message_id||null,
    attachment,
    deleted:false,
    deleted_at:null,
    mentioned_me:false,
    has_mentions:false,
    can_delete:false,
    can_restore:false,
    outbox_state:state||entry.state||'waiting',
    outbox_error:error||entry.last_error||''
  }
}

function sameMessageIdentity(a,b){
  if(!a||!b)return false;
  const aId=Number(a.id);
  const bId=Number(b.id);
  if(
    Number.isFinite(aId)&&aId>0
    &&Number.isFinite(bId)&&bId>0
    &&aId===bId
  )return true;
  const aClient=String(a.client_message_id||'');
  const bClient=String(b.client_message_id||'');
  return !!aClient&&aClient===bClient
}

function activeMatchesOutbox(entry){
  return !!(
    active
    &&active.type===entry?.chat_type
    &&Number(active.data.id)===Number(entry?.chat_id)
  )
}

function replaceMessageByIdentity(oldMessage,newMessage){
  const index=currentMessages.findIndex(item=>sameMessageIdentity(item,oldMessage));
  if(index<0)return false;
  const old=currentMessages[index];
  currentMessages[index]=newMessage;
  const node=document.querySelector(
    '.bubble[data-message-id="'+String(old.id)+'"]'
  );
  if(node)node.replaceWith(msgNode(newMessage));
  return true
}

function setOutboxMessageState(entry,state,error=''){
  if(!entry)return;
  entry.state=state;
  entry.last_error=error||'';
  updateOutboxEntry(entry.id,{
    state,
    last_error:error||'',
    last_attempt_at:new Date().toISOString()
  });
  if(!activeMatchesOutbox(entry))return;
  const local=outboxMessage(entry,state,error);
  const index=currentMessages.findIndex(
    item=>String(item.client_message_id||'')===String(entry.id)
  );
  if(index>=0){
    const previous=currentMessages[index];
    currentMessages[index]=local;
    const node=document.querySelector(
      '.bubble[data-message-id="'+String(previous.id)+'"]'
    );
    if(node)node.replaceWith(msgNode(local))
  }else{
    appendMessage(local)
  }
}

function finalizeOutboxMessage(entry,message){
  if(!entry||!message)return;
  if(!message.client_message_id){
    message={...message,client_message_id:entry.id}
  }
  removeOutbox(entry.id);
  if(!activeMatchesOutbox(entry))return;
  const index=currentMessages.findIndex(item=>
    String(item.client_message_id||'')===String(entry.id)
    ||(
      Number(item.id)>0
      &&Number(item.id)===Number(message.id)
    )
  );
  if(index>=0){
    const previous=currentMessages[index];
    currentMessages[index]=message;
    const node=document.querySelector(
      '.bubble[data-message-id="'+String(previous.id)+'"]'
    );
    if(node)node.replaceWith(msgNode(message))
  }else{
    appendMessage(message)
  }
}

function restoreOutboxForActiveChat(){
  if(!active||!me)return;
  for(const entry of readOutbox()){
    if(!activeMatchesOutbox(entry))continue;

    const alreadyDelivered=currentMessages.find(item=>
      String(item.client_message_id||'')===String(entry.id)
      &&Number(item.id)>0
    );
    if(alreadyDelivered){
      removeOutbox(entry.id);
      continue
    }

    const state=navigator.onLine
      ?(entry.state==='sending'?'waiting':(entry.state||'waiting'))
      :'waiting';
    const pending=outboxMessage(entry,state);
    if(!currentMessages.some(item=>sameMessageIdentity(item,pending))){
      appendMessage(pending)
    }
  }
}

function retryableDeliveryError(err){
  return !err?.status||err.status===408||err.status===425||err.status===429||err.status>=500
}

const deliveryDelay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function clearOutboxFlushTimer(){
  if(outboxFlushTimer){
    clearTimeout(outboxFlushTimer);
    outboxFlushTimer=null
  }
}

function scheduleOutboxFlush(delay=null){
  if(!me||!readOutbox().length)return;
  if(!navigator.onLine)return;
  clearOutboxFlushTimer();
  const wait=delay==null
    ?OUTBOX_RETRY_DELAYS[Math.min(outboxRetryStep,OUTBOX_RETRY_DELAYS.length-1)]
    :Math.max(0,Number(delay)||0);
  outboxFlushTimer=setTimeout(()=>{
    outboxFlushTimer=null;
    flushOutbox().catch(()=>{})
  },wait)
}

async function deliverOutboxEntry(entry){
  if(outboxDeliveries.has(entry.id))return outboxDeliveries.get(entry.id);

  const promise=(async()=>{
    if(!navigator.onLine){
      const offlineError=new Error('Ожидаем интернет');
      offlineError.queued=true;
      setOutboxMessageState(entry,'waiting');
      throw offlineError
    }

    let lastError=new Error('Нет соединения с сервером');
    for(let attempt=0;attempt<3;attempt++){
      if(attempt)await deliveryDelay(attempt===1?700:1800);
      if(!navigator.onLine){
        lastError=new Error('Ожидаем интернет');
        break
      }

      entry.attempts=(Number(entry.attempts)||0)+1;
      setOutboxMessageState(entry,'sending');

      try{
        const message=await api(entry.path,{method:'POST',body:entry.payload});
        finalizeOutboxMessage(entry,message);
        outboxRetryStep=0;
        return message
      }catch(err){
        lastError=err;
        if(!retryableDeliveryError(err)){
          removeOutbox(entry.id);
          setOutboxMessageState(entry,'failed',err?.message||'Не отправлено');
          throw err
        }
      }
    }

    setOutboxMessageState(
      entry,
      'waiting',
      navigator.onLine?(lastError?.message||'Сервер временно недоступен'):''
    );
    lastError.queued=true;
    throw lastError
  })().finally(()=>outboxDeliveries.delete(entry.id));

  outboxDeliveries.set(entry.id,promise);
  return promise
}

async function flushOutbox(){
  if(outboxFlushRunning||!me)return;
  if(!navigator.onLine){
    for(const entry of readOutbox()){
      if(activeMatchesOutbox(entry))setOutboxMessageState(entry,'waiting')
    }
    return
  }

  outboxFlushRunning=true;
  clearOutboxFlushTimer();
  let retryNeeded=false;
  try{
    for(const stored of readOutbox()){
      const entry={...stored};
      try{
        await deliverOutboxEntry(entry);
        if(entry.chat_type==='user')loadUsers().catch(()=>{});
        else loadGroups().catch(()=>{})
      }catch(err){
        if(err?.queued){
          retryNeeded=true;
          break
        }
      }
    }
  }finally{
    outboxFlushRunning=false
  }

  const remaining=readOutbox();
  if(!remaining.length){
    outboxRetryStep=0;
    clearOutboxFlushTimer();
    return
  }

  if(retryNeeded&&navigator.onLine){
    outboxRetryStep=Math.min(
      outboxRetryStep+1,
      OUTBOX_RETRY_DELAYS.length-1
    );
    scheduleOutboxFlush()
  }
}

window.addEventListener('online',()=>{
  outboxRetryStep=0;
  scheduleOutboxFlush(120)
});

window.addEventListener('offline',()=>{
  clearOutboxFlushTimer();
  for(const entry of readOutbox()){
    if(activeMatchesOutbox(entry))setOutboxMessageState(entry,'waiting')
  }
});

function setMode(next){
  mode=next;const reg=next==='register';
  $('tabLogin').classList.toggle('active',!reg);$('tabRegister').classList.toggle('active',reg);
  $('nameField').classList.toggle('hidden',!reg);$('authSubmit').textContent=reg?'Создать аккаунт':'Войти';
  $('forgotPasswordBtn').classList.toggle('hidden',reg);
  $('password').autocomplete=reg?'new-password':'current-password';$('authError').textContent=''
}
$('tabLogin').onclick=()=>setMode('login');$('tabRegister').onclick=()=>setMode('register');

function showRecoveryCode(code,title='Код восстановления',message='Сохрани этот код в надёжном месте. Он показывается только сейчас.'){
  $('recoveryCodeTitle').textContent=title;
  $('recoveryCodeText').textContent=message;
  $('recoveryCodeValue').textContent=code||'';
  $('recoveryCodeDialog').showModal()
}

$('forgotPasswordBtn').onclick=()=>{
  $('passwordRecoveryError').textContent='';
  $('recoveryUsername').value=$('username').value.trim();
  $('recoveryCodeInput').value='';
  $('recoveryNewPassword').value='';
  $('recoveryNewPasswordRepeat').value='';
  $('passwordRecoveryDialog').showModal()
};

$('cancelPasswordRecovery').onclick=()=>$('passwordRecoveryDialog').close();

$('passwordRecoveryForm').onsubmit=async event=>{
  event.preventDefault();
  const button=$('submitPasswordRecovery');
  $('passwordRecoveryError').textContent='';
  const newPassword=$('recoveryNewPassword').value;
  const repeat=$('recoveryNewPasswordRepeat').value;
  if(newPassword!==repeat){
    $('passwordRecoveryError').textContent='Пароли не совпадают';
    return
  }

  button.disabled=true;
  try{
    const data=await api('/api/password/recover',{
      method:'POST',
      body:{
        username:$('recoveryUsername').value.trim(),
        recovery_code:$('recoveryCodeInput').value.trim(),
        new_password:newPassword
      }
    });
    token='';
    localStorage.removeItem('svoi_token');
    $('passwordRecoveryDialog').close();
    await enter();
    showRecoveryCode(
      data.recovery_code,
      'Новый код восстановления',
      'Пароль изменён. Старый код уже недействителен — сохрани этот новый код.'
    )
  }catch(err){
    $('passwordRecoveryError').textContent=err.message||'Не удалось восстановить пароль'
  }finally{
    button.disabled=false
  }
};

$('cancelRecoverySetup').onclick=()=>$('recoverySetupDialog').close();

$('recoverySetupForm').onsubmit=async event=>{
  event.preventDefault();
  const button=$('createRecoveryCodeBtn');
  $('recoverySetupError').textContent='';
  button.disabled=true;
  try{
    const data=await api('/api/account/recovery-code',{
      method:'POST',
      body:{current_password:$('recoveryCurrentPassword').value}
    });
    $('recoveryCurrentPassword').value='';
    $('recoverySetupDialog').close();
    showRecoveryCode(
      data.recovery_code,
      'Новый код восстановления',
      'Сохрани код сейчас. При следующем создании кода этот перестанет работать.'
    )
  }catch(err){
    $('recoverySetupError').textContent=err.message||'Не удалось создать код восстановления'
  }finally{
    button.disabled=false
  }
};

$('copyRecoveryCode').onclick=async()=>{
  const value=$('recoveryCodeValue').textContent.trim();
  if(!value)return;
  try{
    if(navigator.clipboard?.writeText){
      await navigator.clipboard.writeText(value)
    }else{
      const area=document.createElement('textarea');
      area.value=value;
      area.style.position='fixed';
      area.style.opacity='0';
      document.body.append(area);
      area.select();
      document.execCommand('copy');
      area.remove()
    }
    $('copyRecoveryCode').textContent='✅ Скопировано';
    setTimeout(()=>{$('copyRecoveryCode').textContent='📋 Копировать'},1200)
  }catch{
    alert('Не удалось скопировать код')
  }
};

$('closeRecoveryCode').onclick=()=>$('recoveryCodeDialog').close();

$('authForm').onsubmit=async e=>{
  e.preventDefault();$('authError').textContent='';
  try{
    const body={username:$('username').value.trim(),password:$('password').value};
    if(mode==='register')body.display_name=$('displayName').value.trim();
    const data=await api(mode==='register'?'/api/register':'/api/login',{method:'POST',body});
    token='';
    localStorage.removeItem('svoi_token');
    await enter();
    if(data.recovery_code){
      showRecoveryCode(
        data.recovery_code,
        'Код восстановления',
        'Аккаунт создан. Сохрани этот код — без него восстановить пароль автоматически не получится.'
      )
    }
  }catch(err){$('authError').textContent=err.message}
};

function initials(name){return(name||'?').trim().split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase()}
function setAvatar(el,user){
  if(!el)return;
  el.replaceChildren();
  const url=user?.avatar_url;
  el.classList.toggle('has-photo',!!url);
  if(url){
    const img=document.createElement('img');
    img.src=url;
    img.alt=user?.display_name||'Аватар';
    img.loading='lazy';
    el.append(img)
  }else{
    el.textContent=initials(user?.display_name||'?')
  }
}

function setGroupAvatar(el,group){
  if(!el)return;
  el.replaceChildren();
  const url=group?.avatar_url;
  el.classList.add('group-avatar');
  el.classList.toggle('has-photo',!!url);
  if(url){
    const img=document.createElement('img');
    img.src=url;
    img.alt=group?.name||'Аватар группы';
    img.loading='lazy';
    el.append(img)
  }else{
    el.textContent='#'
  }
}
function nativeBadgePlugin(){
  return window.Capacitor?.Plugins?.NativeBadge||null
}

function isActiveChatVisible(chatType,chatId){
  if(document.visibilityState!=='visible')return false;
  if(active?.type!==chatType||Number(active.data.id)!==Number(chatId))return false;
  if($('app')?.classList.contains('hidden'))return false;
  if(window.matchMedia?.('(max-width:720px)').matches){
    return !!$('app')?.classList.contains('chat-open')
  }
  return true
}

let foregroundReadSyncRunning=false;
async function syncForegroundChatRead(){
  if(foregroundReadSyncRunning||!me||!active)return;
  const {type,data}=active;
  const id=Number(data.id);
  if(!isActiveChatVisible(type,id))return;
  foregroundReadSyncRunning=true;
  try{
    if(type==='user'){
      await markPrivateChatRead(id)
    }else if(type==='group'){
      // History marks the selected group read on the server. Do not replace
      // the messages already appended while this window was in the background.
      await api('/api/groups/'+id+'/messages?limit=1')
    }
    await Promise.allSettled([loadUsers(),loadGroups()]);
    await updateAppBadge()
  }finally{
    foregroundReadSyncRunning=false
  }
}

document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='visible'){
    ensureWsConnection();
    syncForegroundChatRead().catch(()=>{})
  }
});

function totalUnreadCount(){
  return [...users,...groups].reduce(
    (sum,item)=>sum+Math.max(0,Number(item?.unread_count)||0),
    0
  )
}

async function updateAppBadge(){
  const count=totalUnreadCount();
  document.title=count>0?'('+count+') Свои':'Свои';

  const plugin=nativeBadgePlugin();
  if(plugin){
    try{
      await plugin.setBadge({count});
      return
    }catch{}
  }

  try{
    if(count>0&&typeof navigator.setAppBadge==='function'){
      await navigator.setAppBadge(count)
    }else if(count===0&&typeof navigator.clearAppBadge==='function'){
      await navigator.clearAppBadge()
    }
  }catch{}
}

function isChatMuted(chatType,chatId){
  const id=Number(chatId);
  const item=chatType==='group'
    ?groups.find(group=>Number(group.id)===id)
    :users.find(user=>Number(user.id)===id);
  if(!item?.muted)return false;
  if(!item.muted_until)return true;
  const until=new Date(item.muted_until);
  return Number.isNaN(until.getTime())||until.getTime()>Date.now()
}

function muteStatusText(item){
  if(!item?.muted)return 'Уведомления включены';
  if(!item.muted_until)return 'Без звука · навсегда';
  const until=new Date(item.muted_until);
  if(Number.isNaN(until.getTime()))return 'Без звука';
  return 'Без звука до '+until.toLocaleString('ru-RU',{
    day:'2-digit',
    month:'2-digit',
    hour:'2-digit',
    minute:'2-digit'
  })
}

function hideStartupSplash(){
  $('startupSplash')?.classList.add('hidden')
}

function showStartupSplash(message='Подключаемся…'){
  const splash=$('startupSplash');
  if(!splash)return;
  const status=$('startupStatus');
  if(status)status.textContent=message;
  splash.classList.remove('hidden')
}

function showAuth(){
  hideStartupSplash();
  me=null;active=null;foundUser=null;
  wsHasConnected=false;
  if(wsReconnectSyncTimer){clearTimeout(wsReconnectSyncTimer);wsReconnectSyncTimer=null}
  if(adminRefreshTimer){clearInterval(adminRefreshTimer);adminRefreshTimer=null}
  if(socket)socket.close();
  $('auth').classList.remove('hidden');
  $('app').classList.add('hidden');
  document.title='Свои';
  updateAppBadge().catch(()=>{})
}

function runWhenIdle(task,timeout=1200){
  if(typeof requestIdleCallback==='function'){
    requestIdleCallback(()=>task(),{timeout})
  }else{
    setTimeout(task,80)
  }
}

function retryStartupLoader(loader,delay=1800){
  setTimeout(()=>loader().catch(()=>{}),delay)
}

async function enter(){
  try{
    await api('/api/session/bootstrap',{method:'POST'});
    token='';
    localStorage.removeItem('svoi_token');
    me=await api('/api/me');
    startClientDiagnostics();
    $('meName').textContent=me.display_name;
    $('meUser').textContent='@'+me.username;
    setAvatar($('meAvatar'),me);
    $('adminNav').classList.toggle('hidden',!me.is_server_admin);
    window.SvoiAdminMasks?.init();
    $('authError').textContent='';
    $('auth').classList.add('hidden');
    $('app').classList.remove('hidden');
    hideStartupSplash();

    // Signaling is critical for incoming calls, so connect it before the
    // heavier chat-list queries finish.
    connectWs();
    resumeIncomingCallFromUrl().catch(()=>{});
    resumeGroupCallFromUrl().catch(()=>{});
    resumeCallInviteFromUrl().catch(()=>{});

    const usersLoad=loadUsers();
    const groupsLoad=loadGroups();
    Promise.allSettled([usersLoad,groupsLoad]).then(results=>{
      if(results[0].status==='rejected')retryStartupLoader(loadUsers);
      if(results[1].status==='rejected')retryStartupLoader(loadGroups);
      resumeNotificationOpen();

      // Offline outbox may refresh chat lists after delivery, so run it only
      // after the first list requests settle to avoid duplicate startup work.
      runWhenIdle(()=>flushOutbox().catch(()=>{}),1400)
    });

    // Push registration and service-worker work are useful but not required
    // for first paint. Call history is now loaded only when the Calls screen opens.
    runWhenIdle(()=>{
      initPush().catch(err=>{
        reportClientError('manual','Ошибка push: '+(err?.message||'неизвестно'),{
          stack:err?.stack||''
        }).catch(()=>{});
        setDrawerButton($('notifyBtn'),'⚠️','Уведомления','Ошибка');
        $('notifyBtn').title=err.message||'Ошибка push'
      })
    },1200)
  }catch(err){
    if(err?.status===401){
      token='';localStorage.removeItem('svoi_token');showAuth();return
    }
    $('app').classList.add('hidden');
    $('auth').classList.add('hidden');
    showStartupSplash('Связь с сервером потеряна · подключаемся снова…');
    setTimeout(()=>enter(),3000)
  }
}

async function loadUsers(){users=await api('/api/users');renderUsers();if(active?.type==='user'){active.data=users.find(u=>u.id===active.data.id)||active.data;updateHead()}updateAppBadge().catch(()=>{})}
async function loadGroups(){groups=await api('/api/groups');renderGroups();if(active?.type==='group'){active.data=groups.find(g=>g.id===active.data.id)||active.data;updateHead()}updateAppBadge().catch(()=>{})}

async function loadCallHistory(){
  callHistory=await api('/api/calls/history?limit=100');
  renderCallHistory()
}

function formatAdminBytes(value){
  let bytes=Math.max(0,Number(value)||0);
  const units=['Б','КБ','МБ','ГБ','ТБ'];
  let index=0;
  while(bytes>=1024&&index<units.length-1){bytes/=1024;index++}
  const digits=index===0?0:(bytes>=100?0:(bytes>=10?1:2));
  return bytes.toFixed(digits)+' '+units[index]
}

function formatAdminUptime(value){
  let seconds=Math.max(0,Math.floor(Number(value)||0));
  const days=Math.floor(seconds/86400);seconds%=86400;
  const hours=Math.floor(seconds/3600);seconds%=3600;
  const minutes=Math.floor(seconds/60);
  if(days)return days+' д '+hours+' ч';
  if(hours)return hours+' ч '+minutes+' мин';
  return minutes+' мин'
}

function adminMetricCard(label,value,detail=''){
  const card=document.createElement('div');card.className='admin-card';
  const small=document.createElement('small');small.textContent=label;
  const strong=document.createElement('strong');strong.textContent=value;
  card.append(small,strong);
  if(detail){const span=document.createElement('span');span.textContent=detail;card.append(span)}
  return card
}

function renderAdminOverview(data){
  const counts=data?.counts||{};
  const activity=data?.activity_24h||{};
  const resources=data?.resources||{};
  const performanceData=data?.performance||{};
  const apiPerf=performanceData.api||{};
  const wsPerf=performanceData.websocket_ping||{};
  const memory=resources.memory||{};
  const disk=resources.disk||{};
  const messages24=(Number(activity.private_messages)||0)+(Number(activity.group_messages)||0);
  const totalMessages=(Number(counts.private_messages)||0)+(Number(counts.group_messages)||0);
  const cards=[
    ['Пользователи',String(counts.users||0),(activity.registrations||0)+' новых за 24 ч'],
    ['Сейчас онлайн',String(data.online_users||0),(data.websocket_connections||0)+' подключений'],
    ['WebSocket',String(data.websocket_connections||0),wsPerf.avg_ms==null?'ping ещё собирается':'средний ping '+Math.round(wsPerf.avg_ms)+' мс · '+(wsPerf.samples||0)+' замеров'],
    ['API p95',apiPerf.p95_ms==null?'—':Math.round(apiPerf.p95_ms)+' мс',apiPerf.avg_ms==null?'за последние 60 с':'среднее '+Math.round(apiPerf.avg_ms)+' мс · максимум '+Math.round(apiPerf.max_ms||0)+' мс'],
    ['API запросы',(Number(apiPerf.requests_per_second)||0).toFixed(2)+'/с',(apiPerf.requests||0)+' за '+(apiPerf.window_seconds||60)+' с · активных '+(apiPerf.active_requests||0)],
    ['API 5xx',String(apiPerf.errors_5xx||0),'за последние '+(apiPerf.window_seconds||60)+' с'],
    ['CPU',resources.cpu_percent==null?'—':resources.cpu_percent+'%','load '+(resources.load_1m??'—')+' · '+(resources.cpu_count||1)+' vCPU'],
    ['Активные звонки',String(data.active_calls||0),(activity.calls||0)+' звонков за 24 ч'],
    ['Сообщения',String(totalMessages),messages24+' за 24 ч'],
    ['Группы',String(counts.groups||0),'Всего создано'],
    ['Файлы',String(counts.uploads||0),formatAdminBytes(counts.uploads_bytes||0)],
    ['RAM',memory.percent==null?'—':memory.percent+'%',formatAdminBytes(memory.used||0)+' / '+formatAdminBytes(memory.total||0)],
    ['Диск',disk.percent==null?'—':disk.percent+'%',formatAdminBytes(disk.used||0)+' / '+formatAdminBytes(disk.total||0)],
    ['Нагрузка 5/15 мин',String(resources.load_5m??'—'),'15м '+(resources.load_15m??'—')],
    ['База данных',formatAdminBytes(resources.database_bytes||0),'Процесс: '+formatAdminBytes(resources.process_rss_bytes||0)],
    ['Сессии',String(counts.sessions||0),'Активные токены входа'],
    ['Аптайм приложения',formatAdminUptime(data.app_uptime_seconds||0),'С последнего запуска']
  ];
  const metrics=$('adminMetrics');metrics.replaceChildren();
  for(const card of cards)metrics.append(adminMetricCard(...card));

  const services=$('adminServices');services.replaceChildren();
  for(const [name,stateRaw] of Object.entries(data.services||{})){
    const state=String(stateRaw||'unknown');
    const row=document.createElement('div');row.className='admin-service';
    const title=document.createElement('strong');title.textContent=name;
    const badge=document.createElement('span');badge.className='admin-service-state '+(state==='active'?'active':(state==='inactive'||state==='failed'?'bad':''));
    badge.textContent=state;
    row.append(title,badge);services.append(row)
  }
}

function adminChartLabel(value,period){
  const date=new Date(value);
  if(period==='week'){
    return date.toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit'})
  }
  return date.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})
}

function svgNode(name,attrs={}){
  const node=document.createElementNS('http://www.w3.org/2000/svg',name);
  for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));
  return node
}

function renderAdminLineChart(container,{title,icon,key,totalText},data){
  const points=Array.isArray(data?.points)?data.points:[];
  const card=document.createElement('div');card.className='admin-chart-card';

  const head=document.createElement('div');head.className='admin-chart-title';
  const name=document.createElement('strong');name.textContent=icon+' '+title;
  const total=document.createElement('span');total.textContent=totalText;
  head.append(name,total);card.append(head);

  if(!points.length){
    const empty=document.createElement('div');empty.className='admin-chart-empty';empty.textContent='Пока нет данных';
    card.append(empty);container.append(card);return
  }

  const width=620,height=180,left=38,right=12,top=15,bottom=31;
  const plotW=width-left-right,plotH=height-top-bottom;
  const values=points.map(point=>Math.max(0,Number(point[key])||0));
  const rawMax=Math.max(...values,0);
  const maxValue=Math.max(1,rawMax);
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':title});

  for(let i=0;i<=2;i++){
    const y=top+(plotH*i/2);
    svg.append(svgNode('line',{x1:left,y1:y,x2:width-right,y2:y,class:'admin-chart-grid-line'}));
    const label=svgNode('text',{x:left-7,y:y+3,'text-anchor':'end',class:'admin-chart-axis'});
    label.textContent=String(Math.round(maxValue*(1-i/2)));
    svg.append(label)
  }

  const coords=values.map((value,index)=>{
    const x=left+(points.length===1?plotW/2:(plotW*index/(points.length-1)));
    const y=top+plotH-(value/maxValue)*plotH;
    return {x,y,value,index}
  });

  if(coords.length>1){
    const areaPath='M '+coords[0].x+' '+(top+plotH)+' L '
      +coords.map(point=>point.x+' '+point.y).join(' L ')
      +' L '+coords[coords.length-1].x+' '+(top+plotH)+' Z';
    svg.append(svgNode('path',{d:areaPath,class:'admin-chart-area'}));
    const linePath='M '+coords.map(point=>point.x+' '+point.y).join(' L ');
    svg.append(svgNode('path',{d:linePath,class:'admin-chart-line'}))
  }

  const dotEvery=data.period==='week'?1:Math.max(1,Math.floor(points.length/12));
  for(const point of coords){
    if(point.index%dotEvery!==0&&point.index!==coords.length-1)continue;
    const dot=svgNode('circle',{cx:point.x,cy:point.y,r:3.5,class:'admin-chart-dot'});
    const tip=svgNode('title');
    tip.textContent=adminChartLabel(points[point.index].start,data.period)+': '+point.value;
    dot.append(tip);svg.append(dot)
  }

  const labelIndexes=data.period==='week'
    ?points.map((_,index)=>index)
    :[0,6,12,18,points.length-1].filter((value,index,array)=>value<points.length&&array.indexOf(value)===index);

  for(const index of labelIndexes){
    const x=left+(points.length===1?plotW/2:(plotW*index/(points.length-1)));
    const label=svgNode('text',{
      x,
      y:height-8,
      'text-anchor':index===0?'start':(index===points.length-1?'end':'middle'),
      class:'admin-chart-axis'
    });
    label.textContent=adminChartLabel(points[index].start,data.period);
    svg.append(label)
  }

  card.append(svg);container.append(card)
}

function renderAdminStats(data){
  const box=$('adminCharts');box.replaceChildren();
  $('adminStatsDay').classList.toggle('active',adminStatsPeriod==='day');
  $('adminStatsWeek').classList.toggle('active',adminStatsPeriod==='week');

  const totals=data?.totals||{};
  const cards=[
    {
      title:'Онлайн',
      icon:'🟢',
      key:'online',
      totalText:'Пик: '+(totals.peak_online||0)
    },
    {
      title:'Сообщения',
      icon:'💬',
      key:'messages',
      totalText:'Всего: '+(totals.messages||0)
    },
    {
      title:'Регистрации',
      icon:'👤',
      key:'registrations',
      totalText:'Всего: '+(totals.registrations||0)
    },
    {
      title:'Звонки',
      icon:'📞',
      key:'calls',
      totalText:'Всего: '+(totals.calls||0)
    }
  ];
  for(const config of cards)renderAdminLineChart(box,config,data);

  if(data?.online_tracking_since){
    $('adminStatsNote').textContent='График онлайна собирается с '
      +new Date(data.online_tracking_since).toLocaleString('ru-RU')
      +'. Остальная статистика построена по сохранённым данным.'
  }else{
    $('adminStatsNote').textContent='История онлайна начнёт собираться после этого обновления.'
  }
}

function formatQualityBitrate(value){
  const n=Number(value);
  if(!Number.isFinite(n)||n<=0)return '—';
  if(n>=1000000)return (n/1000000).toFixed(n>=10000000?0:1)+' Мбит/с';
  return Math.round(n/1000)+' кбит/с'
}

function callEndReasonLabel(reason){
  const labels={
    local_hangup:'завершил пользователь',
    remote_hangup:'завершил собеседник',
    rejected:'отклонён',
    unavailable:'недоступен',
    setup_error:'ошибка запуска',
    accept_error:'ошибка принятия',
    connection_closed:'соединение закрыто',
    promoted_to_conference:'переведён в конференцию',
    local_leave:'выход из группового звонка',
    logout:'выход из аккаунта',
    recovery_failed:'не удалось восстановить связь'
  };
  return labels[String(reason||'')]||String(reason||'')
}

function renderAdminCallQuality(data){
  const summaryBox=$('adminCallQualitySummary');
  const list=$('adminCallQualityList');
  const note=$('adminCallQualityNote');
  summaryBox.replaceChildren();list.replaceChildren();

  const summary=data?.summary||{};
  const cards=[
    ['Диагностировано',summary.calls_with_diagnostics??0],
    ['Средний пинг',summary.avg_rtt_ms==null?'—':Math.round(summary.avg_rtt_ms)+' мс'],
    ['Средние потери',summary.avg_packet_loss_pct==null?'—':Number(summary.avg_packet_loss_pct).toFixed(1)+'%']
  ];
  for(const [label,value] of cards){
    const card=document.createElement('div');card.className='admin-quality-card';
    const small=document.createElement('small');small.textContent=label;
    const strong=document.createElement('strong');strong.textContent=String(value);
    card.append(small,strong);summaryBox.append(card)
  }

  const calls=Array.isArray(data?.calls)?data.calls:[];
  if(!calls.length){
    const empty=document.createElement('div');
    empty.className='admin-call-quality-empty';
    empty.textContent='Диагностика появится после следующего звонка.';
    list.append(empty);
    note.textContent='Снимки качества сохраняются примерно раз в 10 секунд и хранятся 30 дней.';
    return
  }

  for(const call of calls){
    const row=document.createElement('div');row.className='admin-call-quality-row';
    const head=document.createElement('div');head.className='admin-call-quality-head';
    const title=document.createElement('strong');
    const names=(call.participants||[]).join(', ')||'Участник';
    title.textContent=(call.call_type==='private'?'📞 ':'👥 ')+names;
    const when=document.createElement('small');
    when.textContent=call.last_at?new Date(call.last_at).toLocaleString('ru-RU'):'';
    head.append(title,when);

    const meta=document.createElement('div');meta.className='admin-call-quality-meta';
    meta.textContent=(call.call_type==='private'?'Личный звонок':'Групповая диагностика')
      +' · замеров: '+(call.sample_count||0);

    const metrics=document.createElement('div');metrics.className='admin-call-quality-metrics';
    const values=[
      'Ping '+(call.avg_rtt_ms==null?'—':Math.round(call.avg_rtt_ms)+' мс'),
      'Потери '+(call.avg_packet_loss_pct==null?'—':Number(call.avg_packet_loss_pct).toFixed(1)+'%'),
      'Jitter '+(call.avg_jitter_ms==null?'—':Math.round(call.avg_jitter_ms)+' мс'),
      'Видео '+formatQualityBitrate(call.avg_video_bitrate_bps)
    ];
    if(call.max_video_width&&call.max_video_height){
      values.push(call.max_video_width+'×'+call.max_video_height
        +(call.avg_video_fps==null?'':' · '+Math.round(call.avg_video_fps)+' FPS'))
    }
    for(const value of values){
      const chip=document.createElement('span');chip.textContent=value;metrics.append(chip)
    }

    row.append(head,meta,metrics);
    if(call.end_reason){
      const reason=document.createElement('div');reason.className='admin-call-quality-reason';
      reason.textContent='Причина завершения: '+callEndReasonLabel(call.end_reason);
      row.append(reason)
    }
    list.append(row)
  }
  note.textContent='Показываются средние показатели за выбранный период. Диагностика хранится 30 дней.'
}

function clientErrorKindLabel(kind){
  const labels={
    js_error:'JS',
    promise_rejection:'Promise',
    manual:'Событие'
  };
  return labels[String(kind||'')]||String(kind||'Ошибка')
}

function renderAdminClientErrors(data){
  const summaryBox=$('adminClientErrorSummary');
  const list=$('adminClientErrorList');
  const note=$('adminClientErrorNote');
  summaryBox.replaceChildren();
  list.replaceChildren();

  const summary=data?.summary||{};
  const cards=[
    ['Ошибок',summary.total_errors??0],
    ['Пользователей',summary.affected_users??0],
    ['JS',summary.js_errors??0],
    ['Promise',summary.promise_rejections??0]
  ];
  for(const [label,value] of cards){
    const card=document.createElement('div');card.className='admin-quality-card';
    const small=document.createElement('small');small.textContent=label;
    const strong=document.createElement('strong');strong.textContent=String(value);
    card.append(small,strong);summaryBox.append(card)
  }

  const errors=Array.isArray(data?.errors)?data.errors:[];
  if(!errors.length){
    const empty=document.createElement('div');
    empty.className='admin-call-quality-empty';
    empty.textContent='Ошибок клиентов за выбранный период нет.';
    list.append(empty);
    note.textContent='Диагностика не содержит тексты переписок и хранится 30 дней.';
    return
  }

  for(const item of errors){
    const row=document.createElement('div');row.className='admin-client-error-row';
    const head=document.createElement('div');head.className='admin-client-error-head';
    const title=document.createElement('strong');
    title.textContent=item.display_name||item.username||('ID '+item.user_id);
    const kind=document.createElement('span');kind.className='admin-client-error-kind';
    kind.textContent=clientErrorKindLabel(item.kind);title.append(kind);
    const when=document.createElement('small');
    when.textContent=item.recorded_at?new Date(item.recorded_at).toLocaleString('ru-RU'):'';
    head.append(title,when);

    const message=document.createElement('div');message.className='admin-client-error-message';
    message.textContent=item.message||'Неизвестная ошибка';

    const meta=document.createElement('div');meta.className='admin-client-error-meta';
    const chips=[];
    if(item.client_type)chips.push(item.client_type);
    if(item.app_version){
      chips.push('v'+item.app_version+(item.app_version_code!=null?' #'+item.app_version_code:''))
    }
    if(item.effective_type)chips.push('Сеть '+item.effective_type);
    else if(item.network_type)chips.push('Сеть '+item.network_type);
    if(item.network_rtt_ms!=null)chips.push('RTT '+Math.round(item.network_rtt_ms)+' мс');
    if(item.downlink_mbps!=null)chips.push('↓ '+Number(item.downlink_mbps).toFixed(1)+' Мбит/с');
    if(item.online===false)chips.push('offline');
    for(const value of chips){
      const chip=document.createElement('span');chip.textContent=value;meta.append(chip)
    }

    row.append(head,message);
    if(chips.length)row.append(meta);

    const sourceParts=[];
    if(item.source)sourceParts.push(item.source);
    if(item.line_no!=null){
      sourceParts.push('строка '+item.line_no+(item.column_no!=null?':'+item.column_no:''))
    }
    if(item.page_path)sourceParts.push('экран '+item.page_path);
    if(sourceParts.length){
      const source=document.createElement('div');source.className='admin-client-error-source';
      source.textContent=sourceParts.join(' · ');row.append(source)
    }

    if(item.context){
      const context=document.createElement('div');context.className='admin-client-error-context';
      context.textContent=item.context;row.append(context)
    }

    if(item.stack){
      const details=document.createElement('details');details.className='admin-client-error-stack';
      const summaryEl=document.createElement('summary');summaryEl.textContent='Показать стек';
      const pre=document.createElement('pre');pre.textContent=item.stack;
      details.append(summaryEl,pre);row.append(details)
    }
    list.append(row)
  }
  note.textContent='Показаны последние ошибки за выбранный период. Одинаковые ошибки в течение минуты объединяются.'
}

async function setAdminStatsPeriod(period){
  if(!['day','week'].includes(period)||adminStatsPeriod===period)return;
  adminStatsPeriod=period;
  localStorage.setItem('svoi_admin_stats_period',period);
  $('adminStatsDay').classList.toggle('active',period==='day');
  $('adminStatsWeek').classList.toggle('active',period==='week');
  try{
    const [stats,quality,clientErrors]=await Promise.all([
      api('/api/admin/stats?period='+period),
      api('/api/admin/call-quality?period='+period+'&limit=40'),
      api('/api/admin/client-errors?period='+period+'&limit=60')
    ]);
    renderAdminStats(stats);
    renderAdminCallQuality(quality);
    renderAdminClientErrors(clientErrors)
  }catch(err){
    $('adminStatsNote').textContent=err.message||'Не удалось загрузить статистику';
    $('adminCallQualityNote').textContent=err.message||'Не удалось загрузить диагностику звонков';
    $('adminClientErrorNote').textContent=err.message||'Не удалось загрузить ошибки клиентов'
  }
}

async function renameAdminUser(item){
  const current=(item.display_name||item.username||'').trim();
  const value=window.prompt('Новое имя пользователя',current);
  if(value===null)return;
  const displayName=value.trim();
  if(!displayName){window.alert('Имя не может быть пустым');return}
  if(displayName.length>60){window.alert('Имя должно быть не длиннее 60 символов');return}
  try{
    const updated=await api('/api/admin/users/'+item.id+'/display-name',{
      method:'PATCH',
      body:{display_name:displayName}
    });
    item.display_name=updated.display_name;
    await Promise.all([loadAdminPanel(),loadUsers()]);
    if(Number(item.id)===Number(me?.id)){
      me.display_name=updated.display_name;
      $('meName').textContent=updated.display_name
    }
  }catch(err){
    window.alert(err.message||'Не удалось изменить имя пользователя')
  }
}

async function changeAdminUsername(item){
  const current=(item.username||'').trim();
  const value=window.prompt('Новый ник пользователя',current?'@'+current:'');
  if(value===null)return;
  const username=value.trim().replace(/^@/,'').toLowerCase();
  if(!/^[a-z0-9_.-]{3,32}$/.test(username)){
    window.alert('Ник: 3–32 символа. Можно латинские буквы, цифры, _, . и -');
    return
  }
  if(username===current.toLowerCase())return;
  try{
    const updated=await api('/api/admin/users/'+item.id+'/username',{
      method:'PATCH',
      body:{username}
    });
    item.username=updated.username;
    await Promise.all([loadAdminPanel(),loadUsers()]);
    if(Number(item.id)===Number(me?.id)){
      me.username=updated.username
    }
    window.alert('Ник изменён на @'+updated.username+'. При следующем входе пользователь должен использовать новый ник.')
  }catch(err){
    window.alert(err.message||'Не удалось изменить ник пользователя')
  }
}

function renderAdminUsers(items){
  const box=$('adminUsers');box.replaceChildren();
  if(!Array.isArray(items)||!items.length){
    const empty=document.createElement('div');empty.className='muted';empty.textContent='Пользователей пока нет.';box.append(empty);return
  }
  for(const item of items){
    const row=document.createElement('div');row.className='admin-user';
    const dot=document.createElement('span');dot.className='admin-user-online'+(item.online?' on':'');
    const copy=document.createElement('div');copy.className='admin-user-copy';
    const strong=document.createElement('strong');strong.textContent=item.display_name||item.username||('ID '+item.id);
    const small=document.createElement('small');
    const last=item.online?'онлайн':(item.last_seen_at?'был '+new Date(item.last_seen_at).toLocaleString():'ещё не был онлайн');
    small.textContent='@'+item.username+' · '+last+' · сессий: '+(item.session_count||0);
    const edit=document.createElement('button');
    edit.type='button';edit.className='admin-user-edit';edit.textContent='✏️';edit.title='Изменить имя';
    edit.onclick=()=>renameAdminUser(item);
    const editUsername=document.createElement('button');
    editUsername.type='button';editUsername.className='admin-user-edit';editUsername.textContent='@';editUsername.title='Изменить ник';
    editUsername.onclick=()=>changeAdminUsername(item);
    copy.append(strong,small);row.append(dot,copy,edit,editUsername);box.append(row)
  }
}

async function loadAdminPanel(){
  const status=$('adminStatus');
  status.classList.remove('error');status.textContent='Обновляем данные…';
  try{
    const [overview,recentUsers,stats,quality,clientErrors]=await Promise.all([
      api('/api/admin/overview'),
      api('/api/admin/users?limit=50'),
      api('/api/admin/stats?period='+adminStatsPeriod),
      api('/api/admin/call-quality?period='+adminStatsPeriod+'&limit=40'),
      api('/api/admin/client-errors?period='+adminStatsPeriod+'&limit=60')
    ]);
    renderAdminOverview(overview);
    renderAdminUsers(recentUsers);
    renderAdminStats(stats);
    renderAdminCallQuality(quality);
    renderAdminClientErrors(clientErrors);
    const generated=overview.generated_at?new Date(overview.generated_at).toLocaleTimeString():'сейчас';
    status.textContent='Обновлено: '+generated+' · автообновление каждые 10 секунд'
  }catch(err){
    status.classList.add('error');status.textContent=err.message||'Не удалось загрузить админ-панель';
    if(err?.status===403){$('adminNav').classList.add('hidden')}
  }
}

function startAdminRefresh(){
  if(adminRefreshTimer)clearInterval(adminRefreshTimer);
  adminRefreshTimer=setInterval(()=>{
    if($('adminDialog').open)loadAdminPanel().catch(()=>{})
  },10000)
}

function stopAdminRefresh(){
  if(adminRefreshTimer){clearInterval(adminRefreshTimer);adminRefreshTimer=null}
}

function formatCallDuration(seconds){
  const s=Math.max(0,Number(seconds)||0);
  if(!s)return '';
  if(s<60)return s+' сек.';
  const minutes=Math.floor(s/60);
  const rest=s%60;
  return rest?minutes+' мин '+rest+' сек':minutes+' мин'
}

function formatCallTime(value){
  if(!value)return '';
  const date=new Date(value);
  const now=new Date();
  const sameDay=date.toDateString()===now.toDateString();
  return sameDay
    ?date.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})
    :date.toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})
}

function callStatusText(call){
  const direction=call.direction==='incoming'?'Входящий':'Исходящий';
  if(call.status==='missed'){
    return call.direction==='incoming'?'Пропущенный':'Без ответа'
  }
  if(call.status==='rejected'){
    return call.direction==='incoming'?'Отклонён вами':'Отклонён'
  }
  if(call.status==='completed'){
    const duration=formatCallDuration(call.duration_seconds);
    return direction+(duration?' · '+duration:'')
  }
  if(call.status==='answered')return direction+' · соединён';
  return direction+' · вызов'
}

function userForCall(call){
  return users.find(u=>u.id===call.peer_id)||{
    id:call.peer_id,
    username:call.peer_username,
    display_name:call.peer_name,
    online:false
  }
}

function renderCallHistory(){
  const box=$('calls');box.replaceChildren();
  if(!callHistory.length){
    box.innerHTML='<div class="muted" style="padding:8px 12px">История звонков пуста.</div>';
    return
  }
  for(const call of callHistory){
    const row=document.createElement('div');row.className='call-log';
    const main=document.createElement('button');main.className='call-log-main';main.type='button';
    const missed=call.status==='missed'&&call.direction==='incoming';
    main.innerHTML='<span class="call-log-icon '+(missed?'missed':'')+'">'+(call.video?'🎥':'📞')+'</span><span class="call-log-txt"><strong></strong><small class="'+(missed?'missed':'')+'"></small></span>';
    main.querySelector('strong').textContent=call.peer_name;
    main.querySelector('small').textContent=callStatusText(call)+' · '+formatCallTime(call.started_at);
    main.onclick=()=>{
      $('callsDialog').close();
      openUser(userForCall(call))
    };

    const voice=document.createElement('button');voice.className='call-again';voice.type='button';voice.title='Голосовой звонок';voice.textContent='📞';
    voice.onclick=()=>{
      $('callsDialog').close();
      startCallTo(userForCall(call),false)
    };

    const video=document.createElement('button');video.className='call-again';video.type='button';video.title='Видеозвонок';video.textContent='🎥';
    video.onclick=()=>{
      $('callsDialog').close();
      startCallTo(userForCall(call),true)
    };

    row.append(main,voice,video);box.append(row)
  }
}

function formatLastSeen(user){
  if(user?.online)return 'в сети';
  if(!user?.last_seen_at)return 'время последнего входа ещё не записано';

  const date=new Date(user.last_seen_at);
  if(Number.isNaN(date.getTime()))return 'не в сети';

  const now=new Date();
  const sameDay=date.getFullYear()===now.getFullYear()
    && date.getMonth()===now.getMonth()
    && date.getDate()===now.getDate();

  const yesterday=new Date(now);
  yesterday.setDate(now.getDate()-1);
  const wasYesterday=date.getFullYear()===yesterday.getFullYear()
    && date.getMonth()===yesterday.getMonth()
    && date.getDate()===yesterday.getDate();

  const time=date.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
  if(sameDay)return 'был в сети сегодня в '+time;
  if(wasYesterday)return 'был в сети вчера в '+time;

  const dateText=date.toLocaleDateString('ru-RU',{
    day:'2-digit',
    month:'2-digit',
    year:date.getFullYear()===now.getFullYear()?undefined:'numeric'
  });
  return 'был в сети '+dateText+' в '+time
}

function renderUserSearchResult(user=null,message='',isError=false){
  const box=$('userSearchResult');box.replaceChildren();
  if(message){
    const note=document.createElement('div');note.className='search-note'+(isError?' error':'');note.textContent=message;box.append(note);
    return
  }
  if(!user)return;
  const row=document.createElement('div');row.className='search-card';
  const main=document.createElement('button');main.type='button';main.className='search-main';
  const av=document.createElement('span');av.className='avatar';setAvatar(av,user);
  const txt=document.createElement('span');txt.className='txt';
  const name=document.createElement('strong');name.textContent=user.display_name;
  const tag=document.createElement('small');tag.className='muted';tag.textContent='@'+user.username+' · '+(user.blocked_by_me?'🚫 заблокирован':formatLastSeen(user));
  txt.append(name,tag);main.append(av,txt);main.onclick=()=>openUser(user);

  const add=document.createElement('button');add.type='button';add.className='search-add';
  if(user.in_contacts){
    add.textContent='✓ Добавлен';add.disabled=true
  }else{
    add.textContent='Добавить';
    add.onclick=async()=>{
      add.disabled=true;add.textContent='…';
      try{
        foundUser=await api('/api/contacts/'+user.id,{method:'POST'});
        await loadUsers();
        renderUserSearchResult(foundUser)
      }catch(err){
        add.disabled=false;add.textContent='Добавить';
        renderUserSearchResult(null,err.message||'Не удалось добавить контакт',true)
      }
    }
  }
  row.append(main,add);box.append(row)
}

$('userSearchForm').onsubmit=async e=>{
  e.preventDefault();
  const tag=$('userSearchInput').value.trim();
  foundUser=null;
  if(!tag){renderUserSearchResult(null,'Введи тег, например @oleg',true);return}
  renderUserSearchResult(null,'Ищем…');
  try{
    foundUser=await api('/api/users/search?tag='+encodeURIComponent(tag));
    renderUserSearchResult(foundUser)
  }catch(err){
    renderUserSearchResult(null,err.message||'Пользователь не найден',true)
  }
};

function renderUnreadBadge(container,item){
  const mute=container.querySelector('.chat-mute-icon');
  const badge=container.querySelector('.unread-badge');
  if(mute){
    mute.classList.toggle('hidden',!item.muted);
    mute.title=muteStatusText(item)
  }
  if(badge){
    const count=Math.max(0,Number(item.unread_count)||0);
    badge.classList.toggle('hidden',count===0);
    badge.textContent=count>99?'99+':String(count)
  }
}

function draftTextForTarget(target){
  const key=draftKey(target);
  if(!key)return '';
  return (localStorage.getItem(key)||'').trim()
}

function draftPreviewText(value){
  const text=String(value||'').replace(/\s+/g,' ').trim();
  if(!text)return '';
  return text.length>46?text.slice(0,43)+'…':text
}

function updateDraftListPreview(target=active){
  if(!target?.data?.id)return;
  const selector='.item[data-chat-type="'+target.type+'"][data-chat-id="'+Number(target.data.id)+'"]';
  const row=document.querySelector(selector);
  const small=row?.querySelector('.txt small');
  if(!small)return;

  const draft=draftTextForTarget(target);
  small.classList.toggle('draft-preview',!!draft);
  if(draft){
    small.textContent='Черновик: '+draftPreviewText(draft);
    return
  }

  if(target.type==='user'){
    const user=users.find(item=>Number(item.id)===Number(target.data.id))||target.data;
    small.textContent='@'+user.username+' · '+(
      user.blocked_by_me?'🚫 заблокирован':formatLastSeen(user)
    )
  }else{
    const group=groups.find(item=>Number(item.id)===Number(target.data.id))||target.data;
    small.textContent=(group.member_count||0)+' участников'
  }
}

function renderUsers(){
  const box=$('users');box.replaceChildren();
  if(!users.length){box.innerHTML='<div class="muted" style="padding:12px">Контактов и личных диалогов пока нет. Найди человека по @тегу.</div>';return}
  for(const u of users){
    const b=document.createElement('button');
    b.className='item'+(active?.type==='user'&&active.data.id===u.id?' active':'');
    b.dataset.chatType='user';
    b.dataset.chatId=String(u.id);
    b.innerHTML='<span class="avatar"></span><span class="txt"><strong></strong><small class="muted"></small></span><span class="chat-badges"><span class="chat-mute-icon hidden">🔕</span><span class="unread-badge hidden"></span></span><span class="dot '+(u.online?'on':'')+'"></span>';
    setAvatar(b.querySelector('.avatar'),u);
    b.querySelector('strong').textContent=u.display_name;
    const userDraft=draftTextForTarget({type:'user',data:u});
    const userSmall=b.querySelector('small');
    userSmall.classList.toggle('draft-preview',!!userDraft);
    userSmall.textContent=userDraft
      ?'Черновик: '+draftPreviewText(userDraft)
      :('@'+u.username+' · '+(u.blocked_by_me?'🚫 заблокирован':formatLastSeen(u)));
    renderUnreadBadge(b,u);
    b.onclick=()=>openUser(u);
    box.append(b)
  }
}

function renderGroups(){
  const box=$('groups');box.replaceChildren();
  if(!groups.length){box.innerHTML='<div class="muted" style="padding:12px">Групп пока нет.</div>';return}
  for(const g of groups){
    const b=document.createElement('button');
    b.className='item'+(active?.type==='group'&&active.data.id===g.id?' active':'');
    b.dataset.chatType='group';
    b.dataset.chatId=String(g.id);
    b.innerHTML='<span class="avatar group-avatar">#</span><span class="txt"><strong></strong><small class="muted"></small></span><span class="chat-badges"><span class="chat-mute-icon hidden">🔕</span><span class="unread-badge hidden"></span></span>';
    setGroupAvatar(b.querySelector('.avatar'),g);
    b.querySelector('strong').textContent=g.name;
    const groupDraft=draftTextForTarget({type:'group',data:g});
    const groupSmall=b.querySelector('small');
    groupSmall.classList.toggle('draft-preview',!!groupDraft);
    groupSmall.textContent=groupDraft
      ?'Черновик: '+draftPreviewText(groupDraft)
      :(g.member_count+' участников');
    renderUnreadBadge(b,g);
    b.onclick=()=>openGroup(g);
    box.append(b)
  }
}

function chatListRow(type,id){
  const root=type==='group'?$('groups'):$('users');
  if(!root||!id)return null;
  return root.querySelector(
    '.item[data-chat-type="'+type+'"][data-chat-id="'+Number(id)+'"]'
  )
}

function updateUserRow(userId){
  const id=Number(userId);
  const user=users.find(item=>Number(item.id)===id);
  if(!id||!user)return false;
  const row=chatListRow('user',id);
  if(!row)return false;

  row.classList.toggle(
    'active',
    active?.type==='user'&&Number(active.data.id)===id
  );
  setAvatar(row.querySelector('.avatar'),user);
  const strong=row.querySelector('.txt strong');
  if(strong)strong.textContent=user.display_name;

  const draft=draftTextForTarget({type:'user',data:user});
  const small=row.querySelector('.txt small');
  if(small){
    small.classList.toggle('draft-preview',!!draft);
    small.textContent=draft
      ?'Черновик: '+draftPreviewText(draft)
      :('@'+user.username+' · '+(
        user.blocked_by_me?'🚫 заблокирован':formatLastSeen(user)
      ))
  }

  const dot=row.querySelector('.dot');
  if(dot)dot.classList.toggle('on',!!user.online);
  renderUnreadBadge(row,user);
  row.onclick=()=>{
    const fresh=users.find(item=>Number(item.id)===id)||user;
    openUser(fresh)
  };
  return true
}

function updateGroupRow(groupId){
  const id=Number(groupId);
  const group=groups.find(item=>Number(item.id)===id);
  if(!id||!group)return false;
  const row=chatListRow('group',id);
  if(!row)return false;

  row.classList.toggle(
    'active',
    active?.type==='group'&&Number(active.data.id)===id
  );
  setGroupAvatar(row.querySelector('.avatar'),group);
  const strong=row.querySelector('.txt strong');
  if(strong)strong.textContent=group.name;

  const draft=draftTextForTarget({type:'group',data:group});
  const small=row.querySelector('.txt small');
  if(small){
    small.classList.toggle('draft-preview',!!draft);
    small.textContent=draft
      ?'Черновик: '+draftPreviewText(draft)
      :(Number(group.member_count||0)+' участников')
  }

  renderUnreadBadge(row,group);
  row.onclick=()=>{
    const fresh=groups.find(item=>Number(item.id)===id)||group;
    openGroup(fresh)
  };
  return true
}

function updateActiveChatRows(){
  document.querySelectorAll('#users .item.active,#groups .item.active')
    .forEach(row=>row.classList.remove('active'));
  if(!active?.data?.id)return;
  chatListRow(active.type,active.data.id)?.classList.add('active')
}

function currentChatBackgroundTarget(){
  if(!active)return null;
  return {
    chat_type:active.type==='group'?'group':'user',
    chat_id:Number(active.data.id)
  }
}

function applyChatBackgroundVisual(url){
  currentChatBackgroundUrl=url||'';
  const box=$('messages');
  if(!box)return;
  if(url){
    box.classList.add('chat-photo-bg');
    box.style.setProperty('--chat-bg-image','url("'+String(url).replace(/"/g,'%22')+'")')
  }else{
    box.classList.remove('chat-photo-bg');
    box.style.removeProperty('--chat-bg-image')
  }

  const preview=$('chatBackgroundPreview');
  if(preview){
    if(url){
      preview.style.backgroundImage='linear-gradient(#07101e44,#07101e44),url("'+String(url).replace(/"/g,'%22')+'")';
      preview.textContent=''
    }else{
      preview.style.backgroundImage='';
      preview.textContent='Фон не установлен'
    }
  }
  if($('removeChatBackground')){
    $('removeChatBackground').disabled=!url
  }
}

async function loadCurrentChatBackground(){
  const target=currentChatBackgroundTarget();
  if(!target){
    applyChatBackgroundVisual('');
    return
  }
  const request=++chatBackgroundRequest;
  applyChatBackgroundVisual('');
  try{
    const data=await api(
      '/api/chat-background?chat_type='+encodeURIComponent(target.chat_type)
      +'&chat_id='+target.chat_id
    );
    if(request!==chatBackgroundRequest)return;
    const now=currentChatBackgroundTarget();
    if(!now||now.chat_type!==target.chat_type||now.chat_id!==target.chat_id)return;
    applyChatBackgroundVisual(data.background_url||'')
  }catch{
    if(request===chatBackgroundRequest)applyChatBackgroundVisual('')
  }
}

async function openUser(u){
  stopOwnTyping();
  clearReplySource();
  if($('chatMenuDialog').open)$('chatMenuDialog').close();
  if($('forwardDialog').open)$('forwardDialog').close();
  forwardSource=null;
  if($('messageSearchDialog').open)$('messageSearchDialog').close();
  if($('chatBackgroundDialog').open)$('chatBackgroundDialog').close();
  if($('groupMembersDialog').open)$('groupMembersDialog').close();
  if($('groupRenameDialog').open)$('groupRenameDialog').close();

  const request=++privateChatLoadRequest;
  const historyRequest=++messageHistoryRequest;
  const userId=Number(u.id);
  active={type:'user',data:u};
  prewarmPrivateCall(userId).catch(()=>{});
  resetMessageHistoryState();
  $('app').classList.add('chat-open');
  $('chatHead').classList.remove('hidden');
  $('composer').classList.remove('hidden');
  updateHead();
  updateActiveChatRows();
  restoreCurrentDraft();
  loadCurrentChatBackground().catch(()=>{});

  const box=$('messages');
  box.innerHTML='<div class="empty">Загрузка переписки…</div>';

  try{
    const msgs=await api('/api/messages/'+userId+'?limit='+MESSAGE_PAGE_SIZE);
    if(
      request!==privateChatLoadRequest
      || historyRequest!==messageHistoryRequest
      || active?.type!=='user'
      || Number(active.data.id)!==userId
    )return;

    active.data={...active.data,unread_count:0};
    users=users.map(item=>
      Number(item.id)===userId
        ?{...item,unread_count:0}
        :item
    );
    updateUserRow(userId);
    updateAppBadge().catch(()=>{});
    renderMessages(msgs);
    initMessageHistoryState(msgs);
    restoreOutboxForActiveChat();
    $('text').focus()
  }catch(err){
    if(request!==privateChatLoadRequest||historyRequest!==messageHistoryRequest)return;
    box.innerHTML='<div class="empty">Не удалось загрузить переписку</div>';
    throw err
  }
}

async function openGroup(g){
  stopOwnTyping();
  clearReplySource();
  if($('chatMenuDialog').open)$('chatMenuDialog').close();
  if($('chatBackgroundDialog').open)$('chatBackgroundDialog').close();
  if($('groupMembersDialog').open)$('groupMembersDialog').close();
  if($('groupRenameDialog').open)$('groupRenameDialog').close();

  const historyRequest=++messageHistoryRequest;
  const groupId=Number(g.id);
  active={type:'group',data:g};
  closePrewarmedPrivateCall();
  resetMessageHistoryState();
  $('app').classList.add('chat-open');
  $('chatHead').classList.remove('hidden');
  $('composer').classList.remove('hidden');
  updateHead();updateActiveChatRows();restoreCurrentDraft();loadCurrentChatBackground().catch(()=>{});

  const box=$('messages');
  box.innerHTML='<div class="empty">Загрузка переписки…</div>';

  try{
    const msgs=await api('/api/groups/'+groupId+'/messages?limit='+MESSAGE_PAGE_SIZE);
    if(
      historyRequest!==messageHistoryRequest
      || active?.type!=='group'
      || Number(active.data.id)!==groupId
    )return;

    active.data={...active.data,unread_count:0};
    groups=groups.map(item=>Number(item.id)===groupId?{...item,unread_count:0}:item);
    updateGroupRow(groupId);
    updateAppBadge().catch(()=>{});
    renderMessages(msgs);
    initMessageHistoryState(msgs);
    restoreOutboxForActiveChat();
    $('text').focus()
  }catch(err){
    if(historyRequest!==messageHistoryRequest)return;
    box.innerHTML='<div class="empty">Не удалось загрузить переписку</div>';
    throw err
  }
}

function updateHead(){
  if(!active)return;
  if(active.type==='user'){
    const u=active.data;$('chatName').textContent=u.display_name;$('chatAvatar').classList.remove('group-avatar');setAvatar($('chatAvatar'),u);$('chatStatus').textContent=u.blocked_by_me?'в чёрном списке':formatLastSeen(u);
    $('chatAvatar').style.cursor='default';$('chatAvatar').title='Аватар пользователя';
    $('chatName').style.cursor='pointer';$('chatName').title='Меню чата';
    $('groupInfoBtn').classList.add('hidden');
    $('groupMembersBtn').classList.add('hidden');
    $('callActions').classList.remove('hidden');$('audioCallBtn').disabled=!!u.blocked_by_me;$('videoCallBtn').disabled=!!u.blocked_by_me;
    refreshTypingStatus()
  }else{
    const g=active.data;$('chatName').textContent=g.name;setGroupAvatar($('chatAvatar'),g);$('chatStatus').textContent=g.member_count+' участников';
    const canEditAvatar=!!g.is_admin;
    $('chatAvatar').style.cursor=canEditAvatar?'pointer':'default';
    $('chatAvatar').title=canEditAvatar?'Изменить аватар группы':'Аватар группы';
    $('chatName').style.cursor='pointer';
    $('chatName').title='Меню чата';
    $('groupInfoBtn').classList.remove('hidden');
    $('groupMembersBtn').classList.remove('hidden');
    $('callActions').classList.remove('hidden');$('audioCallBtn').disabled=false;$('videoCallBtn').disabled=false;
    refreshTypingStatus()
  }
}

function renderGroupMembers(data){
  groupMembersData=data;
  const box=$('groupMembersList');
  const canAdd=!!active?.data?.is_admin;
  $('groupAddMemberForm').classList.toggle('hidden',!canAdd);
  $('groupAddMemberError').textContent='';
  $('groupAddMemberTag').value='';
  box.replaceChildren();
  $('groupMembersTitle').textContent=active?.type==='group'?active.data.name:'Участники';
  $('groupMembersCount').textContent=(data.members?.length||0)+' участников';

  for(const member of data.members||[]){
    const row=document.createElement('div');
    row.className='group-member-entry';

    const avatar=document.createElement('span');
    avatar.className='avatar';
    setAvatar(avatar,member);

    const info=document.createElement('div');
    info.className='group-member-info';
    const name=document.createElement('strong');
    name.textContent=member.display_name;
    const tag=document.createElement('small');
    tag.className='muted';
    tag.textContent='@'+member.username;

    const meta=document.createElement('div');
    meta.className='group-member-meta';
    if(member.is_owner){
      const badge=document.createElement('span');
      badge.className='role-badge owner';
      badge.textContent='Владелец';
      meta.append(badge)
    }else if(member.is_admin){
      const badge=document.createElement('span');
      badge.className='role-badge admin';
      badge.textContent='Администратор';
      meta.append(badge)
    }

    const online=document.createElement('span');
    online.className='member-online'+(member.online?' on':'');
    online.textContent=(member.online?'● ':'○ ')+formatLastSeen(member);
    meta.append(online);
    info.append(name,tag,meta);
    row.append(avatar,info);

    const actions=document.createElement('div');
    actions.className='member-actions';

    if(data.can_manage_admins&&!member.is_owner){
      const button=document.createElement('button');
      button.className='admin-action'+(member.is_admin?' remove':'');
      button.type='button';
      button.textContent='👑';
      button.title=member.is_admin?'Снять администратора':'Сделать администратором';
      button.setAttribute('aria-label',button.title);
      button.onclick=async()=>{
        button.disabled=true;
        try{
          await api(
            '/api/groups/'+data.group_id+'/admins/'+member.id,
            {method:member.is_admin?'DELETE':'POST'}
          );
          await loadGroupMembers(data.group_id)
        }catch(err){
          alert(err.message||'Не удалось изменить администратора')
        }finally{
          button.disabled=false
        }
      };
      actions.append(button)
    }

    const currentIsOwner=Number(active?.data?.owner_id)===Number(me?.id);
    const canKick=!!active?.data?.is_admin
      && !member.is_owner
      && Number(member.id)!==Number(me?.id)
      && (currentIsOwner||!member.is_admin);

    if(canKick){
      const kick=document.createElement('button');
      kick.className='kick-action';
      kick.type='button';
      kick.textContent='🚪';
      kick.title='Исключить из группы';
      kick.setAttribute('aria-label','Исключить из группы');
      kick.onclick=async()=>{
        const ok=confirm('Исключить '+member.display_name+' из группы?');
        if(!ok)return;
        kick.disabled=true;
        try{
          await api(
            '/api/groups/'+data.group_id+'/members/'+member.id,
            {method:'DELETE'}
          );
          await loadGroupMembers(data.group_id);
          await loadGroups()
        }catch(err){
          alert(err.message||'Не удалось исключить участника')
        }finally{
          kick.disabled=false
        }
      };
      actions.append(kick)
    }

    if(actions.childElementCount)row.append(actions);
    box.append(row)
  }
}

async function loadGroupMembers(groupId){
  const data=await api('/api/groups/'+groupId+'/members');
  renderGroupMembers(data);
  return data
}

$('groupAddMemberForm').onsubmit=async event=>{
  event.preventDefault();
  if(active?.type!=='group'||!active.data.is_admin)return;
  const tag=$('groupAddMemberTag').value.trim();
  if(!tag){
    $('groupAddMemberError').textContent='Укажи @тег пользователя';
    return
  }

  const button=$('groupAddMemberBtn');
  button.disabled=true;
  $('groupAddMemberError').textContent='';
  try{
    await api('/api/groups/'+active.data.id+'/members',{
      method:'POST',
      body:{tag}
    });
    $('groupAddMemberTag').value='';
    await loadGroupMembers(active.data.id);
    await loadGroups()
  }catch(err){
    $('groupAddMemberError').textContent=err.message||'Не удалось добавить участника'
  }finally{
    button.disabled=false
  }
};

async function openGroupMembers(){
  if(active?.type!=='group')return;
  const groupId=active.data.id;
  $('groupMembersTitle').textContent=active.data.name;
  $('groupMembersCount').textContent='Загрузка…';
  $('groupMembersList').innerHTML='<div class="muted" style="padding:14px">Загружаем участников…</div>';
  $('groupMembersDialog').showModal();
  try{
    await loadGroupMembers(groupId)
  }catch(err){
    $('groupMembersList').innerHTML='<div class="error" style="padding:14px"></div>';
    $('groupMembersList').querySelector('.error').textContent=err.message||'Не удалось загрузить участников'
  }
}

function formatSize(bytes){if(bytes<1024)return bytes+' Б';if(bytes<1024*1024)return(Math.round(bytes/102.4)/10)+' КБ';return(Math.round(bytes/1024/102.4)/10)+' МБ'}

function receiptLabel(m){
  if(m.outbox_state==='waiting')return '⏳ Ожидает сети';
  if(m.outbox_state==='sending')return '↻ Отправляется…';
  if(m.outbox_state==='failed')return '⚠ Не отправлено';

  if(active?.type==='group'&&Number(m.sender_id)===Number(me?.id)){
    const summary=m.receipt_summary||{};
    const total=Math.max(0,Number(summary.total_recipients)||0);
    const delivered=Math.max(0,Number(summary.delivered_count)||0);
    const read=Math.max(0,Number(summary.read_count)||0);
    if(total>0){
      return 'Доставлено '+delivered+' из '+total+' · Прочитали '+read
    }
    return 'Нет получателей'
  }

  if(active?.type!=='user'||m.sender_id!==me.id)return '';
  if(m.read_at)return '✓✓ прочитано';
  if(m.delivered_at)return '✓✓';
  return '✓'
}

function formatMediaSeconds(value){
  const total=Math.max(0,Math.round(Number(value)||0));
  const minutes=Math.floor(total/60);
  const seconds=total%60;
  return minutes+':'+String(seconds).padStart(2,'0')
}

let lazyChatMediaObserver=null;

function ensureChatMediaSource(media,eager=false){
  if(!media)return;
  const source=media.dataset?.src;
  if(!source)return;
  media.src=source;
  media.preload='metadata';
  delete media.dataset.src;
  try{lazyChatMediaObserver?.unobserve(media)}catch{}
  if(eager){
    try{media.load()}catch{}
  }
}

function observeChatMedia(media){
  if(!media?.dataset?.src)return;
  if(!('IntersectionObserver' in window)){
    ensureChatMediaSource(media);
    return
  }
  if(!lazyChatMediaObserver){
    lazyChatMediaObserver=new IntersectionObserver(entries=>{
      for(const entry of entries){
        if(entry.isIntersecting){
          ensureChatMediaSource(entry.target)
        }
      }
    },{
      root:$('messages'),
      rootMargin:'700px 0px',
      threshold:0.01
    })
  }
  lazyChatMediaObserver.observe(media)
}

function resetLazyChatMediaObserver(){
  try{lazyChatMediaObserver?.disconnect()}catch{}
  lazyChatMediaObserver=null
}

function replaceMessageNode(message){
  const previous=currentMessages.find(item=>Number(item.id)===Number(message.id));
  if(previous?.receipt_summary&&!message.receipt_summary){
    message={...message,receipt_summary:previous.receipt_summary}
  }
  currentMessages=currentMessages.map(item=>Number(item.id)===Number(message.id)?message:item);
  const node=document.querySelector('.bubble[data-message-id="'+message.id+'"]');
  if(node)node.replaceWith(msgNode(message));

  for(const item of currentMessages){
    if(Number(item.reply_to_message_id)!==Number(message.id))continue;
    const replyNode=document.querySelector('.bubble[data-message-id="'+item.id+'"]');
    if(replyNode)replyNode.replaceWith(msgNode(item))
  }

  if($('messageSearchDialog')?.open){
    performMessageSearch($('messageSearchInput').value)
  }
}

function removeGroupMessageNode(messageId){
  const id=Number(messageId);
  currentMessages=currentMessages.filter(item=>Number(item.id)!==id);
  if(Number(replySource?.id)===id)clearReplySource();
  if(Number(messageActionSource?.message?.id)===id)messageActionSource=null;
  document.querySelector('.bubble[data-message-id="'+id+'"]')?.remove();

  for(const item of currentMessages){
    if(Number(item.reply_to_message_id)!==id)continue;
    const replyNode=document.querySelector('.bubble[data-message-id="'+item.id+'"]');
    if(replyNode)replyNode.replaceWith(msgNode(item))
  }

  const box=$('messages');
  if(!currentMessages.length&&!box.querySelector('.empty')){
    box.innerHTML='<div class="empty">Сообщений пока нет. Напиши первым.</div>'
  }
}

function applyGroupMessageDeleted(groupId,messageId,deletedAt,showDeletedNotice=false){
  if(active?.type!=='group'||Number(active.data.id)!==Number(groupId))return;

  if(!showDeletedNotice){
    removeGroupMessageNode(messageId);
    return
  }

  const node=document.querySelector('.bubble[data-message-id="'+messageId+'"]');
  const existing=currentMessages.find(item=>Number(item.id)===Number(messageId));
  if(!node&&!existing)return;

  const old=node?._message||existing||{
    id:messageId,
    group_id:groupId,
    sender_id:0,
    sender_name:'Участник',
    body:'',
    created_at:new Date().toISOString(),
    attachment:null
  };
  replaceMessageNode({
    ...old,
    body:'',
    attachment:null,
    deleted:true,
    deleted_at:deletedAt||new Date().toISOString(),
    can_delete:false,
    can_restore:!!active.data.is_admin,
    show_deleted_notice:true
  })
}

async function deleteGroupMessageForAll(message){
  if(active?.type!=='group'||!message?.can_delete)return;
  const ok=confirm('Удалить это сообщение для всех участников группы?');
  if(!ok)return;
  try{
    const result=await api(
      '/api/groups/'+active.data.id+'/messages/'+message.id,
      {method:'DELETE'}
    );
    applyGroupMessageDeleted(
      active.data.id,
      message.id,
      result.deleted_at,
      !!result.show_deleted_notice
    )
  }catch(err){
    alert(err.message||'Не удалось удалить сообщение')
  }
}

async function restoreGroupMessage(message){
  if(active?.type!=='group'||!message?.can_restore)return;
  try{
    const restored=await api(
      '/api/groups/'+active.data.id+'/messages/'+message.id+'/restore',
      {method:'POST'}
    );
    replaceMessageNode({
      ...restored,
      can_delete:true,
      can_restore:false
    })
  }catch(err){
    alert(err.message||'Не удалось восстановить сообщение')
  }
}

async function openMentionChat(tag,button=null){
  const username=String(tag||'').trim().replace(/^@/,'').toLowerCase();
  if(!username)return;

  if(me?.username&&String(me.username).toLowerCase()===username){
    alert('Это ваш тег');
    return
  }

  if(button)button.disabled=true;
  try{
    let user=users.find(item=>
      String(item.username||'').toLowerCase()===username
    );

    if(!user){
      user=await api('/api/users/search?tag='+encodeURIComponent('@'+username));
    }

    if(!user?.id)throw new Error('Пользователь не найден');
    await openUser(user)
  }catch(err){
    alert(err?.message||'Не удалось открыть чат с этим пользователем')
  }finally{
    if(button)button.disabled=false
  }
}

function appendRichMessageText(parent,text){
  const parts=String(text||'').split(/(@[A-Za-z0-9_.-]{3,32})/g);
  for(const part of parts){
    if(/^@[A-Za-z0-9_.-]{3,32}$/.test(part)){
      const mention=document.createElement('button');
      mention.type='button';
      mention.className='mention';
      mention.textContent=part;
      mention.title='Открыть чат с '+part;
      mention.onclick=event=>{
        event.preventDefault();
        event.stopPropagation();
        openMentionChat(part,mention)
      };
      parent.append(mention)
    }else if(part){
      parent.append(document.createTextNode(part))
    }
  }
}

function messagePreviewText(message){
  if(!message)return 'Исходное сообщение';
  if(message.deleted)return 'Удалённое сообщение';
  const body=String(message.body||'').replace(/\s+/g,' ').trim();
  if(body)return body.slice(0,140);
  const attachment=message.attachment;
  if(attachment?.is_audio)return '🎙 Голосовое сообщение';
  if(attachment?.is_video)return '◉ Видеокружок';
  if(attachment?.is_image)return '🖼 Фото';
  if(attachment)return '📎 '+(attachment.name||'Файл');
  return 'Сообщение'
}

function messageSenderName(message){
  if(!message)return 'Сообщение';
  if(Number(message.sender_id)===Number(me?.id))return 'Вы';
  if(active?.type==='group')return message.sender_name||'Участник';
  return active?.data?.display_name||'Сообщение'
}

function clearReplySource(){
  replySource=null;
  $('replyPreview')?.classList.add('hidden');
  if($('replyPreviewName'))$('replyPreviewName').textContent='';
  if($('replyPreviewText'))$('replyPreviewText').textContent=''
}

function setReplySource(message){
  if(!active||!message||message.deleted)return;
  replySource=message;
  $('replyPreviewName').textContent='Ответ: '+messageSenderName(message);
  $('replyPreviewText').textContent=messagePreviewText(message);
  $('replyPreview').classList.remove('hidden');
  $('text').focus()
}

function scrollToMessage(messageId){
  const node=document.querySelector('.bubble[data-message-id="'+Number(messageId)+'"]');
  if(!node)return;
  document.querySelectorAll('.bubble.search-hit').forEach(item=>item.classList.remove('search-hit'));
  node.classList.add('search-hit');
  node.scrollIntoView({behavior:'smooth',block:'center'});
  setTimeout(()=>node.classList.remove('search-hit'),2200)
}

function draftKey(target=active){
  if(!me||!target)return '';
  return 'svoi_draft_'+me.id+'_'+target.type+'_'+target.data.id
}

function saveCurrentDraft(){
  const key=draftKey();
  if(!key)return;
  const value=$('text').value;
  if(value.trim())localStorage.setItem(key,value);
  else localStorage.removeItem(key);
  updateDraftListPreview(active)
}

function restoreCurrentDraft(){
  const key=draftKey();
  const value=key?(localStorage.getItem(key)||''):'';
  $('text').value=value;
  resizeComposerTextarea();
  syncSend();
  updateDraftListPreview(active)
}

function clearCurrentDraft(){
  const target=active;
  const key=draftKey(target);
  if(key)localStorage.removeItem(key);
  updateDraftListPreview(target)
}

function sendTypingState(typing){
  if(!active||!socket||socket.readyState!==WebSocket.OPEN)return;
  try{
    socket.send(JSON.stringify({
      type:'typing',
      chat_type:active.type,
      chat_id:Number(active.data.id),
      typing:!!typing
    }))
  }catch{}
}

function stopOwnTyping(){
  if(typingStopTimer){
    clearTimeout(typingStopTimer);
    typingStopTimer=null
  }
  sendTypingState(false);
  lastTypingSentAt=0
}

function handleTypingInput(){
  const hasText=!!$('text').value.trim();
  if(!hasText){
    stopOwnTyping();
    return
  }
  const now=Date.now();
  // Typing is a presence hint, not a per-keystroke event. A slower heartbeat
  // cuts WebSocket traffic substantially while keeping the indicator live.
  if(now-lastTypingSentAt>2500){
    sendTypingState(true);
    lastTypingSentAt=now
  }
  if(typingStopTimer)clearTimeout(typingStopTimer);
  typingStopTimer=setTimeout(()=>{
    sendTypingState(false);
    typingStopTimer=null;
    lastTypingSentAt=0
  },1800)
}

function refreshTypingStatus(){
  if(!active)return;
  const now=Date.now();
  for(const [key,item] of incomingTyping){
    if(item.expires<=now)incomingTyping.delete(key)
  }
  const matches=[...incomingTyping.values()].filter(item=>
    item.chat_type===active.type
    && Number(item.chat_id)===Number(active.data.id)
  );
  if(!matches.length){
    if(active.type==='user')$('chatStatus').textContent=formatLastSeen(active.data);
    else $('chatStatus').textContent=active.data.member_count+' участников';
    return
  }
  if(active.type==='user'){
    $('chatStatus').textContent='печатает…'
  }else{
    const names=matches.slice(0,2).map(item=>item.from_name);
    $('chatStatus').textContent=names.join(', ')+(matches.length>2?' и ещё':'')+' печатает…'
  }
}

function performMessageSearch(query){
  const needle=String(query||'').trim().toLowerCase();
  const box=$('messageSearchResults');
  box.replaceChildren();
  if(!needle){
    $('messageSearchCount').textContent='';
    return
  }
  const matches=currentMessages.filter(m=>{
    const text=[m.body,m.sender_name,m.attachment?.name].filter(Boolean).join(' ').toLowerCase();
    return text.includes(needle)
  });
  $('messageSearchCount').textContent='Найдено: '+matches.length;
  if(!matches.length){
    box.innerHTML='<div class="muted" style="padding:12px">Совпадений нет.</div>';
    return
  }
  for(const m of matches.slice().reverse()){
    const button=document.createElement('button');
    button.type='button';
    button.className='message-search-result';
    const preview=(m.body||m.attachment?.name||'Медиа').slice(0,120);
    button.textContent=preview;
    const meta=document.createElement('small');
    const sender=m.sender_name||(m.sender_id===me?.id?'Вы':'Сообщение');
    meta.textContent=sender+' · '+new Date(m.created_at).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
    button.append(meta);
    button.onclick=()=>{
      $('messageSearchDialog').close();
      const node=document.querySelector('.bubble[data-message-id="'+m.id+'"]');
      if(!node)return;
      document.querySelectorAll('.bubble.search-hit').forEach(item=>item.classList.remove('search-hit'));
      node.classList.add('search-hit');
      node.scrollIntoView({behavior:'smooth',block:'center'});
      setTimeout(()=>node.classList.remove('search-hit'),2200)
    };
    box.append(button)
  }
}

function openEditMessage(message){
  if(
    !active
    || !message
    || message.deleted
    || Number(message.sender_id)!==Number(me?.id)
  )return;

  editSource={
    type:active.type,
    chat_id:Number(active.data.id),
    message_id:Number(message.id)
  };
  $('editMessageText').value=message.body||'';
  $('editMessageError').textContent='';
  $('editMessageDialog').showModal();
  setTimeout(()=>{
    $('editMessageText').focus();
    $('editMessageText').setSelectionRange(
      $('editMessageText').value.length,
      $('editMessageText').value.length
    )
  },0)
}

function closeEditMessageDialog(){
  editSource=null;
  $('editMessageError').textContent='';
  if($('editMessageDialog').open)$('editMessageDialog').close()
}

async function saveEditedMessage(){
  if(!editSource)return;
  const source={...editSource};
  const message=currentMessages.find(
    item=>Number(item.id)===source.message_id
  );
  const body=$('editMessageText').value.trim();

  if(!body&&!message?.attachment){
    $('editMessageError').textContent='Сообщение не может быть пустым';
    return
  }

  const button=$('saveEditMessage');
  button.disabled=true;
  $('editMessageError').textContent='';
  try{
    const endpoint=source.type==='group'
      ?'/api/groups/'+source.chat_id+'/messages/'+source.message_id
      :'/api/messages/'+source.message_id;
    const updated=await api(endpoint,{
      method:'PATCH',
      body:{body}
    });

    if(
      active
      && active.type===source.type
      && Number(active.data.id)===source.chat_id
    ){
      replaceMessageNode(updated)
    }
    closeEditMessageDialog()
  }catch(err){
    $('editMessageError').textContent=err.message||'Не удалось изменить сообщение'
  }finally{
    button.disabled=false
  }
}

function closeMessageActions(){
  messageActionSource=null;
  if($('messageActionsDialog').open)$('messageActionsDialog').close()
}

function openMessageActions(message){
  if(!active||!message||message.deleted)return;
  messageActionSource={
    type:active.type,
    chat_id:Number(active.data.id),
    message
  };

  $('messageActionsTitle').textContent=messageSenderName(message);
  $('messageActionsPreview').textContent=messagePreviewText(message);

  const mine=Number(message.sender_id)===Number(me?.id);
  $('messageActionEdit').classList.toggle('hidden',!mine);
  $('messageActionDeleteAll').classList.toggle(
    'hidden',
    !(mine||(active.type==='group'&&message.can_delete))
  );

  $('messageActionsDialog').showModal()
}

function messageCopyText(message){
  if(!message)return '';
  const body=String(message.body||'').trim();
  if(body)return body;
  if(message.attachment?.url){
    return new URL(message.attachment.url,location.origin).href
  }
  if(message.attachment?.name)return String(message.attachment.name);
  return ''
}

async function copyMessageFromMenu(){
  const source=messageActionSource;
  if(!source)return;
  const text=messageCopyText(source.message);
  if(!text){
    alert('В сообщении нечего копировать');
    return
  }

  try{
    if(navigator.clipboard?.writeText){
      await navigator.clipboard.writeText(text)
    }else{
      const area=document.createElement('textarea');
      area.value=text;
      area.style.position='fixed';
      area.style.opacity='0';
      document.body.append(area);
      area.focus();
      area.select();
      const ok=document.execCommand('copy');
      area.remove();
      if(!ok)throw new Error('Не удалось скопировать')
    }
    closeMessageActions()
  }catch(err){
    alert(err?.message||'Не удалось скопировать сообщение')
  }
}

async function deleteMessageForMe(){
  const source=messageActionSource;
  if(!source)return;
  const messageId=Number(source.message.id);
  const path=source.type==='group'
    ?'/api/groups/'+source.chat_id+'/messages/'+messageId+'/me'
    :'/api/messages/'+messageId+'/me';

  try{
    await api(path,{method:'DELETE'});
    closeMessageActions();
    if(
      active
      && active.type===source.type
      && Number(active.data.id)===source.chat_id
    )removeGroupMessageNode(messageId)
  }catch(err){
    alert(err.message||'Не удалось удалить сообщение')
  }
}

async function deleteMessageForAllFromMenu(){
  const source=messageActionSource;
  if(!source)return;
  const message=source.message;
  const ok=confirm('Удалить это сообщение для всех?');
  if(!ok)return;
  closeMessageActions();

  if(source.type==='group'){
    if(
      active?.type==='group'
      && Number(active.data.id)===source.chat_id
    ){
      try{
        const result=await api(
          '/api/groups/'+source.chat_id+'/messages/'+message.id,
          {method:'DELETE'}
        );
        applyGroupMessageDeleted(
          source.chat_id,
          message.id,
          result.deleted_at,
          !!result.show_deleted_notice
        )
      }catch(err){
        alert(err.message||'Не удалось удалить сообщение')
      }
    }
    return
  }

  try{
    await api('/api/messages/'+message.id+'/all',{method:'DELETE'});
    if(
      active?.type==='user'
      && Number(active.data.id)===source.chat_id
    )removeGroupMessageNode(message.id)
  }catch(err){
    alert(err.message||'Не удалось удалить сообщение для всех')
  }
}

function formatReceiptTime(value){
  if(!value)return '';
  return new Date(value).toLocaleString(
    'ru-RU',
    {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}
  )
}

function renderSeenBy(data){
  const box=$('seenByList');box.replaceChildren();
  const recipients=Array.isArray(data?.recipients)?data.recipients:null;

  if(recipients){
    const summary=data?.summary||{};
    const total=Math.max(0,Number(summary.total_recipients)||0);
    const delivered=Math.max(0,Number(summary.delivered_count)||0);
    const read=Math.max(0,Number(summary.read_count)||0);
    $('seenByTitle').textContent='Доставка сообщения';
    $('seenByCount').textContent=
      'Доставлено '+delivered+' из '+total+' · Прочитали '+read;

    if(!recipients.length){
      const empty=document.createElement('div');
      empty.className='seen-empty';
      empty.textContent='У сообщения нет других получателей.';
      box.append(empty);
      return
    }

    for(const viewer of recipients){
      const state=viewer.read_at
        ?'read'
        :(viewer.delivered_at?'delivered':'waiting');
      const row=document.createElement('div');
      row.className='seen-entry '+state;

      const avatar=document.createElement('span');
      avatar.className='avatar';
      setAvatar(avatar,viewer);

      const copy=document.createElement('div');
      copy.className='seen-copy';
      const name=document.createElement('strong');
      name.textContent=
        Number(viewer.id)===Number(me?.id)
          ?'Вы'
          :viewer.display_name;

      const meta=document.createElement('small');
      if(viewer.read_at){
        meta.textContent=
          '✓✓ Прочитано · '+formatReceiptTime(viewer.read_at)
      }else if(viewer.delivered_at){
        meta.textContent=
          '✓ Доставлено · '+formatReceiptTime(viewer.delivered_at)
      }else{
        meta.textContent='○ Ожидает доставки'
      }

      copy.append(name,meta);
      row.append(avatar,copy);
      box.append(row)
    }
    return
  }

  const items=Array.isArray(data?.viewers)
    ?data.viewers
    :(Array.isArray(data)?data:[]);
  $('seenByTitle').textContent='Кто просмотрел';
  $('seenByCount').textContent='Просмотрели: '+items.length;

  if(!items.length){
    const empty=document.createElement('div');
    empty.className='seen-empty';
    empty.textContent='Пока никто не просмотрел';
    box.append(empty);
    return
  }

  for(const viewer of items){
    const row=document.createElement('div');row.className='seen-entry read';
    const avatar=document.createElement('span');avatar.className='avatar';
    setAvatar(avatar,viewer);
    const copy=document.createElement('div');copy.className='seen-copy';
    const name=document.createElement('strong');
    name.textContent=Number(viewer.id)===Number(me?.id)?'Вы':viewer.display_name;
    const meta=document.createElement('small');
    meta.textContent='@'+viewer.username+' · '+(
      viewer.read_at
        ?formatReceiptTime(viewer.read_at)
        :'просмотрено'
    );
    copy.append(name,meta);row.append(avatar,copy);box.append(row)
  }
}

async function openSeenBySource(source){
  if(!source)return;
  const path=source.type==='group'
    ?'/api/groups/'+source.chat_id+'/messages/'+source.message.id+'/seen-by'
    :'/api/messages/'+source.message.id+'/seen-by';

  try{
    const result=await api(path);
    if($('messageActionsDialog').open)$('messageActionsDialog').close();
    renderSeenBy(result);
    $('seenByDialog').showModal()
  }catch(err){
    alert(err.message||'Не удалось получить статусы')
  }
}

async function openSeenBy(){
  return openSeenBySource(messageActionSource)
}

async function openSeenByForMessage(message){
  if(!active||!message)return;
  return openSeenBySource({
    type:active.type,
    chat_id:Number(active.data.id),
    message
  })
}

function renderForwardTargets(){
  const box=$('forwardList');
  box.replaceChildren();

  const targets=[
    ...users.map(user=>({
      type:'user',
      id:user.id,
      title:user.display_name,
      subtitle:'@'+user.username,
      avatar:user
    })),
    ...groups.map(group=>({
      type:'group',
      id:group.id,
      title:group.name,
      subtitle:(group.member_count||0)+' участников',
      avatar:group,
      group:true
    }))
  ];

  if(!targets.length){
    box.innerHTML='<div class="muted" style="padding:12px">Нет доступных чатов для пересылки.</div>';
    return
  }

  for(const target of targets){
    const button=document.createElement('button');
    button.type='button';
    button.className='forward-target';

    const avatar=document.createElement('span');
    avatar.className='avatar'+(target.group?' group-avatar':'');
    if(target.group)setGroupAvatar(avatar,target.avatar);
    else setAvatar(avatar,target.avatar);

    const text=document.createElement('span');
    text.className='txt';
    const title=document.createElement('strong');
    title.textContent=target.title;
    const sub=document.createElement('small');
    sub.textContent=target.subtitle;
    text.append(title,sub);

    button.append(avatar,text);
    button.onclick=()=>forwardMessageTo(target);
    box.append(button)
  }
}

function openForwardDialog(message){
  if(!active||!message||message.deleted)return;
  forwardSource={
    source_type:active.type,
    source_message_id:Number(message.id)
  };
  $('forwardStatus').textContent='Выбери личный чат или группу';
  renderForwardTargets();
  $('forwardDialog').showModal()
}

async function forwardMessageTo(target){
  if(!forwardSource)return;
  const buttons=[...$('forwardList').querySelectorAll('button')];
  buttons.forEach(button=>button.disabled=true);
  $('forwardStatus').textContent='Пересылаем…';
  try{
    await api('/api/messages/forward',{
      method:'POST',
      body:{
        ...forwardSource,
        target_type:target.type,
        target_chat_id:Number(target.id)
      }
    });
    $('forwardDialog').close();
    forwardSource=null
  }catch(err){
    $('forwardStatus').textContent=err.message||'Не удалось переслать сообщение';
    buttons.forEach(button=>button.disabled=false)
  }
}

function bindSwipeToReply(node,message){
  if(!node||!message||message.deleted)return;
  node.classList.add('swipe-reply-ready');

  const indicator=document.createElement('span');
  indicator.className='swipe-reply-indicator';
  indicator.textContent='↩';
  indicator.setAttribute('aria-hidden','true');
  node.append(indicator);

  const threshold=68;
  const maxDistance=96;
  let gesture=null;

  const reset=(animated=true)=>{
    if(animated){
      node.style.transition='transform .16s ease-out'
    }else{
      node.style.transition=''
    }
    node.style.transform='';
    indicator.style.opacity='0';
    indicator.style.transform='scale(.72)';
    node.classList.remove('swipe-reply-threshold');
    if(animated){
      setTimeout(()=>{
        if(!gesture)node.style.transition=''
      },180)
    }
  };

  node.addEventListener('pointerdown',event=>{
    if(event.pointerType==='mouse'||!event.isPrimary)return;
    if(message.deleted)return;
    if(event.target.closest('button,a,audio,video,input,textarea,.circle-player,.voice-wave'))return;

    gesture={
      pointerId:event.pointerId,
      startX:event.clientX,
      startY:event.clientY,
      active:false,
      cancelled:false,
      crossed:false,
      dx:0
    };
    node.style.transition=''
  });

  node.addEventListener('pointermove',event=>{
    if(!gesture||gesture.pointerId!==event.pointerId||gesture.cancelled)return;
    const dx=event.clientX-gesture.startX;
    const dy=event.clientY-gesture.startY;

    if(!gesture.active){
      if(Math.abs(dy)>10&&Math.abs(dy)>Math.max(8,dx)){
        gesture.cancelled=true;
        reset(false);
        return
      }
      if(dx<=6)return;
      if(dx<Math.abs(dy)*1.15)return;
      gesture.active=true;
      try{node.setPointerCapture(event.pointerId)}catch{}
    }

    if(dx<0){
      gesture.dx=0;
      node.style.transform='';
      indicator.style.opacity='0';
      indicator.style.transform='scale(.72)';
      node.classList.remove('swipe-reply-threshold');
      return
    }

    event.preventDefault();
    gesture.dx=dx;
    const rendered=dx<=threshold
      ?dx
      :threshold+(Math.min(dx,maxDistance)-threshold)*0.42;
    const progress=Math.max(0,Math.min(1,dx/threshold));
    node.style.transform='translate3d('+rendered+'px,0,0)';
    indicator.style.opacity=String(Math.min(1,progress*1.15));
    indicator.style.transform='scale('+(0.72+0.28*progress)+')';

    const crossed=dx>=threshold;
    node.classList.toggle('swipe-reply-threshold',crossed);
    if(crossed&&!gesture.crossed){
      gesture.crossed=true;
      try{performVibration([24])}catch{}
    }else if(!crossed){
      gesture.crossed=false
    }
  },{passive:false});

  const finish=event=>{
    if(!gesture||gesture.pointerId!==event.pointerId)return;
    const shouldReply=gesture.active&&!gesture.cancelled&&gesture.dx>=threshold;
    const moved=gesture.active&&gesture.dx>10;
    gesture=null;
    if(moved)node._suppressMessageClickUntil=Date.now()+450;
    reset(true);
    if(shouldReply){
      setReplySource(message)
    }
  };

  node.addEventListener('pointerup',finish);
  node.addEventListener('pointercancel',event=>{
    if(!gesture||gesture.pointerId!==event.pointerId)return;
    gesture=null;
    reset(true)
  })
}

function msgNode(m){
  const pending=!!m.outbox_state;
  const d=document.createElement('div');
  d.className='bubble'+(m.sender_id===me.id?' mine':'')+(pending?' outbox-pending':'');
  d.dataset.messageId=String(m.id);d._message=m;
  d.addEventListener('click',event=>{
    if(m.deleted||pending)return;
    if((d._suppressMessageClickUntil||0)>Date.now()){
      event.preventDefault();
      event.stopPropagation();
      return
    }
    if(event.target.closest('button,a,audio,video,input,textarea,.circle-player'))return;
    openMessageActions(m)
  });
  d.addEventListener('contextmenu',event=>{
    if(m.deleted||pending)return;
    event.preventDefault();
    openMessageActions(m)
  });
  if(!pending)bindSwipeToReply(d,m);
  if(active?.type==='group'&&m.mentioned_me&&m.sender_id!==me.id&&!m.deleted)d.classList.add('mentioned-me');
  if(active?.type==='group'&&m.sender_id!==me.id){const s=document.createElement('div');s.className='sender';s.textContent=m.sender_name||'Участник';d.append(s)}
  if(active?.type==='group'&&m.mentioned_me&&m.sender_id!==me.id&&!m.deleted){
    const mentionLabel=document.createElement('span');
    mentionLabel.className='mentioned-me-label';
    mentionLabel.textContent='🔔 Вас упомянули';
    d.append(mentionLabel)
  }
  if(m.forwarded&&!m.deleted){
    const forwarded=document.createElement('span');
    forwarded.className='forwarded-label';
    forwarded.textContent='↪ Переслано';
    d.append(forwarded)
  }
  if(m.reply_to_message_id&&!m.deleted){
    const original=currentMessages.find(item=>Number(item.id)===Number(m.reply_to_message_id));
    const quote=document.createElement('button');
    quote.type='button';
    quote.className='reply-quote';
    quote.title=original?'Перейти к исходному сообщению':'Исходное сообщение не загружено';
    const author=document.createElement('strong');
    author.textContent=original?messageSenderName(original):'Ответ на сообщение';
    const preview=document.createElement('span');
    preview.textContent=messagePreviewText(original);
    quote.append(author,preview);
    if(original){
      quote.onclick=event=>{
        event.stopPropagation();
        scrollToMessage(m.reply_to_message_id)
      }
    }else{
      quote.disabled=true
    }
    d.append(quote)
  }
  if(m.deleted){
    d.classList.add('deleted-message');
    const note=document.createElement('span');
    note.className='deleted-note';
    note.textContent=m.show_deleted_notice===false?'':'Сообщение удалено';
    d.append(note)
  }else{
    if(m.body)appendRichMessageText(d,m.body);
  }
  if(!m.deleted&&m.attachment){
    const mime=m.attachment.mime_type||'';
    const attachmentName=(m.attachment.name||'').toLowerCase();
    const isVoice=!!m.attachment.is_audio||mime.startsWith('audio/')||attachmentName.startsWith('voice-');
    const isCircle=!!m.attachment.is_video||mime.startsWith('video/')||attachmentName.startsWith('video-circle-');
    if(!m.body&&(isVoice||isCircle)){
      d.classList.add('media-only',isVoice?'voice-only':'circle-only')
    }
    if(m.attachment.is_image){
      const img=document.createElement('img');
      img.className='attachment-img';
      img.src=m.attachment.thumbnail_url||m.attachment.url;
      img.alt=m.attachment.name;
      img.loading='lazy';
      img.decoding='async';
      img.dataset.fullSrc=m.attachment.url;
      img.onerror=()=>{
        if(img.src!==new URL(m.attachment.url,location.href).href){
          img.src=m.attachment.url
        }
      };
      img.onclick=event=>{
        event.stopPropagation();
        if(img.dataset.fullSrc)window.open(img.dataset.fullSrc,'_blank','noopener')
      };
      try{img.fetchPriority='low'}catch{}
      d.append(img)
    }else if(isVoice){
      const player=document.createElement('div');
      player.className='voice-message';

      const audio=document.createElement('audio');
      audio.preload='none';
      audio.dataset.src=m.attachment.url;
      observeChatMedia(audio);

      const play=document.createElement('button');
      play.className='voice-play';
      play.type='button';
      play.textContent='▶';
      play.setAttribute('aria-label','Воспроизвести голосовое');

      const main=document.createElement('div');
      main.className='voice-main';

      const wave=document.createElement('div');
      wave.className='voice-wave';
      wave.setAttribute('role','slider');
      wave.setAttribute('aria-label','Позиция голосового сообщения');
      wave.setAttribute('aria-valuemin','0');
      wave.setAttribute('aria-valuemax','100');
      wave.setAttribute('aria-valuenow','0');

      const seed=String(m.id||'')+'|'+String(m.attachment.name||'')+'|'+String(m.attachment.url||'');
      let hash=2166136261;
      for(let i=0;i<seed.length;i++){
        hash^=seed.charCodeAt(i);
        hash=Math.imul(hash,16777619)
      }
      const bars=[];
      for(let i=0;i<38;i++){
        hash^=hash<<13;hash^=hash>>>17;hash^=hash<<5;
        const bar=document.createElement('span');
        bar.className='voice-bar';
        const value=(Math.abs(hash)%100)/100;
        bar.style.height=(7+Math.round(value*19))+'px';
        wave.append(bar);
        bars.push(bar)
      }

      const bottom=document.createElement('div');
      bottom.className='voice-bottom';

      const time=document.createElement('span');
      time.className='voice-time';
      time.textContent='0:00';

      const speed=document.createElement('button');
      speed.className='voice-speed';
      speed.type='button';
      speed.textContent='1×';
      speed.setAttribute('aria-label','Скорость воспроизведения 1×');

      const renderVoiceProgress=()=>{
        const duration=Number.isFinite(audio.duration)?audio.duration:0;
        const current=Number.isFinite(audio.currentTime)?audio.currentTime:0;
        const progress=duration>0?Math.max(0,Math.min(1,current/duration)):0;
        const played=Math.round(progress*bars.length);
        bars.forEach((bar,index)=>bar.classList.toggle('played',index<played));
        wave.setAttribute('aria-valuenow',String(Math.round(progress*100)));
        time.textContent=formatMediaSeconds(audio.paused&&current===0?duration:current)+' / '+formatMediaSeconds(duration)
      };

      const seekVoice=event=>{
        const duration=Number.isFinite(audio.duration)?audio.duration:0;
        if(!duration)return;
        const rect=wave.getBoundingClientRect();
        const clientX=event.touches?.[0]?.clientX??event.clientX;
        const ratio=Math.max(0,Math.min(1,(clientX-rect.left)/Math.max(1,rect.width)));
        audio.currentTime=ratio*duration;
        renderVoiceProgress()
      };

      play.onclick=event=>{
        event.stopPropagation();
        if(audio.paused||audio.ended){
          document.querySelectorAll('.voice-message audio').forEach(other=>{
            if(other!==audio&&!other.paused)other.pause()
          });
          ensureChatMediaSource(audio,true);
          audio.play().catch(()=>{})
        }else{
          audio.pause()
        }
      };

      wave.addEventListener('click',event=>{
        event.stopPropagation();
        seekVoice(event)
      });

      speed.onclick=event=>{
        event.stopPropagation();
        const speeds=[1,1.5,2];
        const current=speeds.indexOf(audio.playbackRate);
        const next=speeds[(current+1)%speeds.length];
        audio.playbackRate=next;
        speed.textContent=String(next).replace('.0','')+'×';
        speed.setAttribute('aria-label','Скорость воспроизведения '+speed.textContent)
      };

      audio.addEventListener('loadedmetadata',renderVoiceProgress);
      audio.addEventListener('durationchange',renderVoiceProgress);
      audio.addEventListener('timeupdate',renderVoiceProgress);
      audio.addEventListener('play',()=>{
        player.classList.add('playing');
        play.textContent='❚❚';
        play.setAttribute('aria-label','Поставить голосовое на паузу')
      });
      audio.addEventListener('pause',()=>{
        player.classList.remove('playing');
        play.textContent='▶';
        play.setAttribute('aria-label','Воспроизвести голосовое');
        renderVoiceProgress()
      });
      audio.addEventListener('ended',()=>{
        player.classList.remove('playing');
        play.textContent='▶';
        try{audio.currentTime=0}catch{}
        renderVoiceProgress()
      });

      bottom.append(time,speed);
      main.append(wave,bottom);
      player.append(play,main,audio);
      audio.hidden=true;
      d.append(player)
    }else if(isCircle){
      const player=document.createElement('div');
      player.className='circle-player';

      const video=document.createElement('video');
      video.className='video-circle-message';
      video.controls=false;
      video.preload='none';
      video.playsInline=true;
      if(m.attachment.thumbnail_url)video.poster=m.attachment.thumbnail_url;
      video.dataset.src=m.attachment.url;
      video.disablePictureInPicture=true;
      observeChatMedia(video);

      const play=document.createElement('button');
      play.className='circle-play';
      play.type='button';
      play.textContent='▶';
      play.setAttribute('aria-label','Воспроизвести видеокружок');

      const duration=document.createElement('span');
      duration.className='circle-duration';
      duration.textContent='0:00';

      const updateDuration=()=>{
        const full=Number.isFinite(video.duration)?video.duration:0;
        const shown=video.paused||video.ended
          ?full
          :Math.max(0,full-video.currentTime);
        duration.textContent=formatMediaSeconds(shown)
      };

      video.addEventListener('loadedmetadata',updateDuration);
      video.addEventListener('durationchange',updateDuration);
      video.addEventListener('timeupdate',updateDuration);
      video.addEventListener('play',()=>{
        player.classList.add('playing');
        updateDuration()
      });
      video.addEventListener('pause',()=>{
        player.classList.remove('playing');
        updateDuration()
      });
      video.addEventListener('ended',()=>{
        player.classList.remove('playing');
        try{video.currentTime=0}catch{}
        updateDuration()
      });

      const togglePlay=()=>{
        if(video.paused||video.ended){
          ensureChatMediaSource(video,true);
          video.play().catch(()=>{})
        }else{
          video.pause()
        }
      };
      player.addEventListener('click',event=>{
        event.preventDefault();
        togglePlay()
      });

      player.append(video,play);
      const wrap=document.createElement('div');
      wrap.className='circle-message-wrap';
      wrap.append(player,duration);
      d.append(wrap)
    }else{
      const a=document.createElement('a');a.className='attachment-file';a.href=m.attachment.url;a.target='_blank';a.rel='noopener';
      const icon=document.createElement('span');icon.textContent='📄';
      const text=document.createElement('span');text.textContent=m.attachment.name+' · '+formatSize(m.attachment.size);
      a.append(icon,text);d.append(a)
    }
  }
  const meta=document.createElement('div');meta.className='meta';
  if(active?.type==='group'&&m.deleted&&m.can_restore){
    const restore=document.createElement('button');
    restore.className='restore-action';
    restore.type='button';
    restore.textContent='↩ Восстановить';
    restore.onclick=event=>{event.stopPropagation();restoreGroupMessage(m)};
    meta.append(restore)
  }
  if(m.edited_at&&!m.deleted){
    const edited=document.createElement('span');
    edited.className='edited-label';
    edited.textContent='изменено';
    edited.title='Изменено '+new Date(m.edited_at).toLocaleString('ru-RU');
    meta.append(edited)
  }
  const t=document.createElement('time');t.textContent=new Date(m.created_at).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});meta.append(t);
  const label=receiptLabel(m);
  if(label){
    const canOpenReceiptDetails=(
      active?.type==='group'
      &&Number(m.sender_id)===Number(me?.id)
      &&Number(m.id)>0
      &&!!m.receipt_summary
    );
    const r=document.createElement(canOpenReceiptDetails?'button':'span');
    if(canOpenReceiptDetails)r.type='button';
    r.className='receipt'
      +(m.read_at?' read':'')
      +(m.outbox_state?' '+m.outbox_state:'')
      +(canOpenReceiptDetails?' receipt-details':'');
    r.textContent=label;
    if(m.outbox_error)r.title=m.outbox_error;
    if(canOpenReceiptDetails){
      r.title='Показать доставку и прочтение';
      r.onclick=event=>{
        event.stopPropagation();
        openSeenByForMessage(m).catch(()=>{})
      }
    }
    meta.append(r)
  }
  d.append(meta);return d
}

function messagesNearBottom(box=$('messages'),threshold=140){
  if(!box)return true;
  return box.scrollHeight-box.scrollTop-box.clientHeight<=threshold
}

function scrollMessagesToBottom(behavior='auto'){
  const box=$('messages');
  if(!box)return;
  if(typeof box.scrollTo==='function'){
    box.scrollTo({top:box.scrollHeight,behavior})
  }else{
    box.scrollTop=box.scrollHeight
  }
}

function currentMessageHistoryKey(target=active){
  if(!target?.data?.id)return '';
  return target.type+':'+Number(target.data.id)
}

function resetMessageHistoryState(){
  messageHistoryLoading=false;
  messageHistoryHasMore=false;
  messageHistoryKey=currentMessageHistoryKey();
  messageHistoryReadyAt=Date.now()+700
}

function initMessageHistoryState(items){
  messageHistoryKey=currentMessageHistoryKey();
  messageHistoryLoading=false;
  messageHistoryHasMore=Array.isArray(items)&&items.length>=MESSAGE_PAGE_SIZE;
  messageHistoryReadyAt=Date.now()+700
}

function renderMessages(items){
  currentMessages=[...items];
  const box=$('messages');
  resetLazyChatMediaObserver();
  box.replaceChildren();

  if(!items.length){
    box.innerHTML='<div class="empty">Сообщений пока нет. Напиши первым.</div>';
  }else{
    const fragment=document.createDocumentFragment();
    for(const m of items)fragment.append(msgNode(m));
    box.append(fragment)
  }

  requestAnimationFrame(()=>scrollMessagesToBottom('auto'))
}

function prependMessages(items){
  if(!Array.isArray(items)||!items.length)return 0;
  const existing=new Set(currentMessages.map(item=>Number(item.id)));
  const fresh=items.filter(item=>!existing.has(Number(item.id)));
  if(!fresh.length)return 0;

  const box=$('messages');
  const fragment=document.createDocumentFragment();
  for(const m of fresh)fragment.append(msgNode(m));
  box.prepend(fragment);
  currentMessages=[...fresh,...currentMessages];
  return fresh.length
}

async function loadOlderMessages(){
  if(
    messageHistoryLoading
    || !messageHistoryHasMore
    || !active
    || !currentMessages.length
    || Date.now()<messageHistoryReadyAt
  )return;

  const key=currentMessageHistoryKey();
  if(!key||key!==messageHistoryKey)return;

  const serverIds=currentMessages
    .map(item=>Number(item.id))
    .filter(value=>Number.isFinite(value)&&value>0);
  const oldestId=serverIds.length?Math.min(...serverIds):null;
  if(!Number.isFinite(oldestId)||oldestId<=1){
    messageHistoryHasMore=false;
    return
  }

  messageHistoryLoading=true;
  const box=$('messages');
  const oldHeight=box.scrollHeight;
  const oldTop=box.scrollTop;
  const targetType=active.type;
  const targetId=Number(active.data.id);
  const path=targetType==='user'
    ?'/api/messages/'+targetId+'?limit='+MESSAGE_PAGE_SIZE+'&before_id='+oldestId
    :'/api/groups/'+targetId+'/messages?limit='+MESSAGE_PAGE_SIZE+'&before_id='+oldestId;

  try{
    const items=await api(path);
    if(currentMessageHistoryKey()!==key)return;
    if(!Array.isArray(items)||items.length<MESSAGE_PAGE_SIZE){
      messageHistoryHasMore=false
    }
    const added=prependMessages(items);
    if(added){
      requestAnimationFrame(()=>{
        if(currentMessageHistoryKey()!==key)return;
        box.scrollTop=oldTop+(box.scrollHeight-oldHeight)
      })
    }
  }catch(err){
    console.warn('history pagination failed',err)
  }finally{
    if(currentMessageHistoryKey()===key){
      messageHistoryLoading=false
    }
  }
}

$('messages').addEventListener('scroll',()=>{
  if($('messages').scrollTop<220){
    loadOlderMessages().catch(()=>{})
  }
},{passive:true});

function appendMessage(m){
  const existingIndex=currentMessages.findIndex(item=>sameMessageIdentity(item,m));
  if(existingIndex>=0){
    const existing=currentMessages[existingIndex];
    if(existing.outbox_state&&!m.outbox_state){
      currentMessages[existingIndex]=m;
      const node=document.querySelector(
        '.bubble[data-message-id="'+String(existing.id)+'"]'
      );
      if(node)node.replaceWith(msgNode(m));
      if(m.client_message_id)removeOutbox(m.client_message_id)
    }
    return false
  }

  const box=$('messages');
  const shouldFollow=
    Number(m.sender_id)===Number(me?.id)
    || messagesNearBottom(box);

  currentMessages.push(m);
  box.querySelector('.empty')?.remove();
  box.append(msgNode(m));

  if(shouldFollow){
    requestAnimationFrame(()=>scrollMessagesToBottom('smooth'))
  }
  return true
}

async function markPrivateChatRead(userId){
  const id=Number(userId);
  if(!id)return;
  const result=await api('/api/messages/'+id+'/read',{method:'POST'});
  if(
    active?.type==='user'
    && Number(active.data.id)===id
    && (result?.message_ids||[]).length
  ){
    const ids=new Set(result.message_ids.map(Number));
    currentMessages=currentMessages.map(item=>
      ids.has(Number(item.id))
        ?{...item,read_at:result.read_at||item.read_at}
        :item
    )
  }
}

function schedulePrivateUsersRefresh(delay=180){
  if(privateUsersRefreshTimer)clearTimeout(privateUsersRefreshTimer);
  privateUsersRefreshTimer=setTimeout(()=>{
    privateUsersRefreshTimer=null;
    loadUsers().catch(()=>{})
  },delay)
}

function applyPrivateMessageToChatList(message){
  const myId=Number(me?.id);
  const senderId=Number(message?.sender_id);
  const recipientId=Number(message?.recipient_id);
  const peerId=senderId===myId?recipientId:senderId;
  if(!peerId)return;

  const index=users.findIndex(item=>Number(item.id)===peerId);
  if(index<0){
    schedulePrivateUsersRefresh(120);
    return
  }

  const activeHere=isActiveChatVisible('user',peerId);
  const incoming=senderId!==myId;
  const previous=users[index];
  const nextUnread=incoming
    ?(activeHere?0:Number(previous.unread_count||0)+1)
    :Number(previous.unread_count||0);

  const updated={...previous,unread_count:nextUnread};
  users=[
    ...users.slice(0,index),
    updated,
    ...users.slice(index+1)
  ];

  if(activeHere){
    active.data={...active.data,...updated,unread_count:0}
  }

  if(!updateUserRow(peerId))renderUsers();
  updateAppBadge().catch(()=>{})
}

function syncSend(){
  const recordingBusy=!!messageRecorder||!!messageRecordBlob;
  const blocked=isActiveUserBlocked();
  const hasText=!!$('text').value.trim();

  $('composer').classList.toggle('has-text',hasText);

  $('send').disabled=blocked||uploading||recordingBusy||(!hasText&&!pendingAttachment);
  $('attach').disabled=blocked||uploading||recordingBusy;
  $('voiceRecordBtn').disabled=blocked||uploading||recordingBusy;
  $('videoCircleBtn').disabled=blocked||uploading||recordingBusy;
  $('text').disabled=blocked;
  $('text').placeholder=blocked?'Пользователь в чёрном списке':'Сообщение…'
}

function clearPending(){
  pendingAttachment=null;$('pendingFile').classList.add('hidden');$('pendingFileName').textContent='';$('fileInput').value='';syncSend()
}

function clearComposerAfterQueued(body,attachmentId,replyToMessageId){
  if($('text').value.trim()===body){
    $('text').value='';
    resizeComposerTextarea();
    clearCurrentDraft();
    stopOwnTyping()
  }
  if((pendingAttachment?.id||null)===(attachmentId||null))clearPending();
  else syncSend();
  if((replySource?.id||null)===(replyToMessageId||null))clearReplySource()
}

function supportedRecorderMime(kind){
  const candidates=kind==='video'
    ?['video/webm;codecs=vp8,opus','video/webm','video/mp4']
    :['audio/webm;codecs=opus','audio/webm','audio/mp4'];
  if(!window.MediaRecorder)return '';
  return candidates.find(type=>MediaRecorder.isTypeSupported?.(type))||''
}

function recordingTargetMatches(target){
  if(!target||!active)return false;
  return target.type===active.type&&Number(target.id)===Number(active.data.id)
}

function recordingTargetFromActive(){
  if(!active)return null;
  return {
    type:active.type,
    id:active.data.id,
    reply_to_message_id:replySource?.id||null
  }
}

function formatRecordingTime(ms){
  const total=Math.max(0,Math.floor(ms/1000));
  const minutes=Math.floor(total/60);
  const seconds=total%60;
  return String(minutes).padStart(2,'0')+':'+String(seconds).padStart(2,'0')
}

function stopRecordCanvas(){
  if(messageRecordCanvasRaf){
    cancelAnimationFrame(messageRecordCanvasRaf);
    messageRecordCanvasRaf=null
  }
  messageRecordCanvas=null
}

function stopStreamTracks(stream){
  if(!stream)return;
  for(const track of stream.getTracks()){
    try{track.stop()}catch{}
  }
}

function stopRecordTracks(){
  stopRecordCanvas();
  stopStreamTracks(messageRecordStream);
  stopStreamTracks(messageMicStream);
  stopStreamTracks(messageCameraStream);
  messageRecordStream=null;
  messageMicStream=null;
  messageCameraStream=null
}

function clearRecordObjectUrl(){
  if(messageRecordObjectUrl){
    URL.revokeObjectURL(messageRecordObjectUrl);
    messageRecordObjectUrl=''
  }
}

function stopRecordingWaveform(){
  if(recordingWaveRaf){
    cancelAnimationFrame(recordingWaveRaf);
    recordingWaveRaf=null
  }
  recordingAnalyser=null;
  if(recordingAudioContext){
    try{recordingAudioContext.close()}catch{}
    recordingAudioContext=null
  }
  const canvas=$('recordingWaveform');
  if(canvas){
    const ctx=canvas.getContext('2d');
    ctx?.clearRect(0,0,canvas.width,canvas.height)
  }
}

function startRecordingWaveform(stream){
  stopRecordingWaveform();
  const AudioCtx=window.AudioContext||window.webkitAudioContext;
  if(!AudioCtx)return;
  try{
    recordingAudioContext=new AudioCtx();
    const source=recordingAudioContext.createMediaStreamSource(stream);
    recordingAnalyser=recordingAudioContext.createAnalyser();
    recordingAnalyser.fftSize=256;
    recordingAnalyser.smoothingTimeConstant=.78;
    source.connect(recordingAnalyser);

    const canvas=$('recordingWaveform');
    const ctx=canvas?.getContext('2d');
    if(!canvas||!ctx)return;
    const values=new Uint8Array(recordingAnalyser.frequencyBinCount);

    const draw=()=>{
      if(!recordingAnalyser||!ctx)return;
      recordingAnalyser.getByteFrequencyData(values);
      ctx.clearRect(0,0,canvas.width,canvas.height);
      const bars=30;
      const step=Math.max(1,Math.floor(values.length/bars));
      const gap=2;
      const barWidth=(canvas.width-gap*(bars-1))/bars;
      for(let i=0;i<bars;i++){
        const value=values[i*step]/255;
        const height=Math.max(3,value*canvas.height*.9);
        const x=i*(barWidth+gap);
        const y=(canvas.height-height)/2;
        ctx.fillStyle='rgba(142,196,255,'+(0.35+value*.65)+')';
        ctx.fillRect(x,y,barWidth,height)
      }
      recordingWaveRaf=requestAnimationFrame(draw)
    };
    draw()
  }catch{}
}

function resetMessageRecorder(){
  if(messageRecordTimer){
    clearInterval(messageRecordTimer);
    messageRecordTimer=null
  }
  stopRecordTracks();
  stopRecordingWaveform();
  clearRecordObjectUrl();
  messageRecorder=null;
  messageRecordChunks=[];
  messageRecordKind=null;
  messageRecordStartedAt=0;
  messageRecordBlob=null;
  messageRecordMime='';
  messageRecordTarget=null;
  messageRecordAction=null;
  $('recordingPreview').pause();
  $('recordingPreview').srcObject=null;
  $('recordingPreview').removeAttribute('src');
  $('recordingPreview').classList.add('hidden');
  $('recordSwitchCamera').classList.add('hidden');
  $('recordSwitchCamera').disabled=false;
  messageRecordFacing='user';
  $('recordingAudioIcon').classList.remove('hidden');
  $('recordingPanel').classList.remove('video-recording');
  $('recordingPanel').classList.add('hidden');
  $('voiceRecordBtn').classList.remove('active');
  $('videoCircleBtn').classList.remove('active');
  syncSend()
}

function setRecordedPreview(blob,kind){
  const preview=$('recordingPreview');
  if(kind!=='video')return;
  clearRecordObjectUrl();
  messageRecordObjectUrl=URL.createObjectURL(blob);
  preview.srcObject=null;
  preview.src=messageRecordObjectUrl;
  preview.style.transform='none';
  preview.loop=true;
  preview.muted=true;
  preview.play().catch(()=>{})
}

async function uploadAndSendRecorded(blob,kind,mime,target){
  if(!blob||!target)return;
  if(blob.size>20*1024*1024){
    throw new Error('Запись получилась больше 20 МБ')
  }

  uploading=true;
  syncSend();
  $('recordingTitle').textContent='Отправляем…';
  $('recordSend').disabled=true;
  $('recordCancel').disabled=true;

  const ext=mime.includes('mp4')?(kind==='audio'?'m4a':'mp4'):'webm';
  const name=(kind==='video'?'video-circle-':'voice-')+Date.now()+'.'+ext;
  const form=new FormData();
  form.append('file',blob,name);

  const uploadResponse=await fetch('/api/uploads',{
    method:'POST',
    headers:{...authHeaders()},
    body:form
  });
  let attachment=null;
  try{attachment=await uploadResponse.json()}catch{}
  if(!uploadResponse.ok)throw new Error(attachment?.detail||'Не удалось загрузить запись');

  const payload={
    body:'',
    attachment_id:attachment.id,
    reply_to_message_id:target.reply_to_message_id||null
  };
  const message=target.type==='user'
    ?await api('/api/messages',{method:'POST',body:{recipient_id:target.id,...payload}})
    :await api('/api/groups/'+target.id+'/messages',{method:'POST',body:payload});

  if(recordingTargetMatches(target)){
    appendMessage(message);
    if((replySource?.id||null)===(target.reply_to_message_id||null))clearReplySource()
  }
}

function startVideoCanvasCapture(preview,micStream){
  const canvas=document.createElement('canvas');
  canvas.width=480;
  canvas.height=480;
  const ctx=canvas.getContext('2d',{alpha:false});
  if(!ctx||typeof canvas.captureStream!=='function'){
    throw new Error('Браузер не поддерживает переключение камеры во время записи')
  }

  const draw=()=>{
    if(!messageRecordCanvas||!ctx)return;
    const vw=preview.videoWidth||480;
    const vh=preview.videoHeight||480;
    const side=Math.min(vw,vh);
    const sx=Math.max(0,(vw-side)/2);
    const sy=Math.max(0,(vh-side)/2);
    ctx.fillStyle='#050b14';
    ctx.fillRect(0,0,480,480);
    try{ctx.drawImage(preview,sx,sy,side,side,0,0,480,480)}catch{}
    messageRecordCanvasRaf=requestAnimationFrame(draw)
  };

  messageRecordCanvas=canvas;
  draw();

  const canvasStream=canvas.captureStream(30);
  return new MediaStream([
    ...canvasStream.getVideoTracks(),
    ...micStream.getAudioTracks(),
  ])
}

async function openMessageCamera(facing){
  let stream=null;
  try{
    stream=await navigator.mediaDevices.getUserMedia({
      audio:false,
      video:{
        facingMode:{exact:facing},
        width:{ideal:480},
        height:{ideal:480}
      }
    })
  }catch{
    stream=await navigator.mediaDevices.getUserMedia({
      audio:false,
      video:{
        facingMode:{ideal:facing},
        width:{ideal:480},
        height:{ideal:480}
      }
    })
  }
  return stream
}

async function startMessageRecording(kind){
  if(!active)return;
  if(currentCall||pendingCall||groupCallState){
    alert('Сначала заверши текущий звонок');
    return
  }
  if(messageRecorder||messageRecordBlob)return;
  if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){
    alert('Этот браузер не поддерживает запись сообщений');
    return
  }

  const target=recordingTargetFromActive();
  const isVideo=kind==='video';
  let stream=null;
  try{
    if(isVideo){
      const combined=await navigator.mediaDevices.getUserMedia({
        audio:true,
        video:{
          facingMode:{ideal:messageRecordFacing},
          width:{ideal:480},
          height:{ideal:480}
        }
      });
      messageMicStream=new MediaStream(combined.getAudioTracks());
      messageCameraStream=new MediaStream(combined.getVideoTracks());
      stream=combined
    }else{
      stream=await navigator.mediaDevices.getUserMedia({audio:true,video:false});
      messageMicStream=stream
    }

    const mime=supportedRecorderMime(kind);
    const options={};
    if(mime)options.mimeType=mime;
    if(isVideo){
      options.videoBitsPerSecond=900000;
      options.audioBitsPerSecond=64000
    }else{
      options.audioBitsPerSecond=48000
    }

    if(isVideo){
      $('recordingPreview').style.transform=messageRecordFacing==='user'?'scaleX(-1)':'none';
      $('recordingPreview').srcObject=messageCameraStream;
      await $('recordingPreview').play().catch(()=>{});
      messageRecordStream=startVideoCanvasCapture($('recordingPreview'),messageMicStream)
    }else{
      messageRecordStream=messageMicStream
    }

    const recorder=new MediaRecorder(messageRecordStream,options);
    messageRecorder=recorder;
    messageRecordChunks=[];
    messageRecordKind=kind;
    messageRecordStartedAt=Date.now();
    messageRecordBlob=null;
    messageRecordMime=recorder.mimeType||mime||(isVideo?'video/webm':'audio/webm');
    messageRecordTarget=target;
    messageRecordAction=null;

    $('recordingPanel').classList.toggle('video-recording',isVideo);
    $('recordingPanel').classList.remove('hidden');
    $('recordingTitle').textContent=isVideo?'Записываем видеокружок':'Записываем голосовое';
    $('recordingTimer').textContent='00:00';
    $('recordSend').disabled=false;
    $('recordCancel').disabled=false;
    $('recordingAudioIcon').classList.toggle('hidden',isVideo);
    $('recordingWaveform').classList.toggle('hidden',isVideo);
    $('recordingPreview').classList.toggle('hidden',!isVideo);
    $('recordSwitchCamera').classList.toggle('hidden',!isVideo);
    $('voiceRecordBtn').classList.toggle('active',!isVideo);
    $('videoCircleBtn').classList.toggle('active',isVideo);

    if(!isVideo){
      startRecordingWaveform(messageMicStream)
    }

    recorder.ondataavailable=event=>{
      if(event.data?.size)messageRecordChunks.push(event.data)
    };

    recorder.onerror=()=>{
      alert('Ошибка записи');
      resetMessageRecorder()
    };

    recorder.onstop=async()=>{
      if(messageRecordTimer){
        clearInterval(messageRecordTimer);
        messageRecordTimer=null
      }
      stopRecordingWaveform();

      const action=messageRecordAction||'ready';
      const finalMime=recorder.mimeType||messageRecordMime||(isVideo?'video/webm':'audio/webm');
      const blob=new Blob(messageRecordChunks,{type:finalMime});
      stopRecordTracks();
      messageRecorder=null;
      messageRecordChunks=[];
      messageRecordBlob=blob;
      messageRecordMime=finalMime;

      if(action==='cancel'){
        resetMessageRecorder();
        return
      }

      if(!blob.size){
        alert('Запись получилась пустой');
        resetMessageRecorder();
        return
      }

      if(action==='send'){
        try{
          await uploadAndSendRecorded(blob,kind,finalMime,target);
          resetMessageRecorder()
        }catch(err){
          $('recordingTitle').textContent='Не удалось отправить';
          $('recordSend').disabled=false;
          $('recordCancel').disabled=false;
          alert(err.message||'Не удалось отправить запись')
        }finally{
          uploading=false;
          syncSend()
        }
        return
      }

      $('recordingTitle').textContent=isVideo?'Видеокружок готов':'Голосовое готово';
      $('recordingTimer').textContent=formatRecordingTime(Date.now()-messageRecordStartedAt);
      setRecordedPreview(blob,kind);
      syncSend()
    };

    recorder.start(250);
    const maxMs=isVideo?60000:300000;
    messageRecordTimer=setInterval(()=>{
      const elapsed=Date.now()-messageRecordStartedAt;
      $('recordingTimer').textContent=formatRecordingTime(elapsed);
      if(elapsed>=maxMs&&messageRecorder?.state==='recording'){
        messageRecordAction='ready';
        messageRecorder.stop()
      }
    },250);
    syncSend()
  }catch(err){
    resetMessageRecorder();
    if(err?.name==='NotAllowedError'){
      alert(isVideo?'Разреши доступ к камере и микрофону':'Разреши доступ к микрофону')
    }else{
      alert(err?.message||'Не удалось начать запись')
    }
  }
}

async function switchMessageRecordingCamera(){
  if(
    messageRecordKind!=='video'
    || !messageRecorder
    || messageRecorder.state!=='recording'
  )return;

  const button=$('recordSwitchCamera');
  const next=messageRecordFacing==='user'?'environment':'user';
  button.disabled=true;

  try{
    const nextStream=await openMessageCamera(next);
    const previous=messageCameraStream;
    messageCameraStream=nextStream;
    messageRecordFacing=next;

    const preview=$('recordingPreview');
    preview.style.transform=next==='user'?'scaleX(-1)':'none';
    preview.srcObject=nextStream;
    await preview.play().catch(()=>{});

    stopStreamTracks(previous)
  }catch(err){
    alert('Не удалось переключить камеру на этом устройстве')
  }finally{
    button.disabled=false
  }
}

$('recordSwitchCamera').onclick=()=>switchMessageRecordingCamera();

async function sendMessageRecording(){
  if(messageRecorder?.state==='recording'){
    messageRecordAction='send';
    $('recordSend').disabled=true;
    messageRecorder.stop();
    return
  }
  if(!messageRecordBlob||!messageRecordTarget)return;

  const blob=messageRecordBlob;
  const kind=messageRecordKind;
  const mime=messageRecordMime;
  const target=messageRecordTarget;
  try{
    await uploadAndSendRecorded(blob,kind,mime,target);
    resetMessageRecorder()
  }catch(err){
    $('recordingTitle').textContent='Не удалось отправить';
    $('recordSend').disabled=false;
    $('recordCancel').disabled=false;
    alert(err.message||'Не удалось отправить запись')
  }finally{
    uploading=false;
    syncSend()
  }
}

function cancelMessageRecording(){
  if(messageRecorder?.state==='recording'){
    messageRecordAction='cancel';
    messageRecorder.stop();
    return
  }
  resetMessageRecorder()
}

$('voiceRecordBtn').onclick=()=>startMessageRecording('audio');
$('videoCircleBtn').onclick=()=>startMessageRecording('video');
$('recordSend').onclick=()=>sendMessageRecording();
$('recordCancel').onclick=()=>cancelMessageRecording();

$('chatAvatar').onclick=()=>{
  if(active?.type!=='group')return;
  if(!active.data.is_admin)return;
  $('groupAvatarInput').click()
};

$('groupAvatarInput').onchange=async()=>{
  const file=$('groupAvatarInput').files?.[0];
  $('groupAvatarInput').value='';
  if(!file||active?.type!=='group')return;
  if(!active.data.is_admin){
    alert('Менять аватар группы может только администратор');
    return
  }
  if(file.size>5*1024*1024){
    alert('Аватар должен быть не больше 5 МБ');
    return
  }
  if(!['image/jpeg','image/png','image/webp'].includes(file.type)){
    alert('Выбери JPEG, PNG или WebP');
    return
  }

  const groupId=active.data.id;
  const form=new FormData();
  form.append('file',file);
  $('chatAvatar').style.opacity='.55';
  try{
    const r=await fetch('/api/groups/'+groupId+'/avatar',{
      method:'POST',
      headers:{...authHeaders()},
      body:form
    });
    let data=null;
    try{data=await r.json()}catch{}
    if(!r.ok)throw new Error(data?.detail||'Не удалось изменить аватар группы');

    groups=groups.map(g=>g.id===data.id?data:g);
    if(active?.type==='group'&&active.data.id===data.id)active.data=data;
    renderGroups();
    updateHead()
  }catch(err){
    alert(err.message||'Не удалось изменить аватар группы')
  }finally{
    $('chatAvatar').style.opacity=''
  }
};

function resizeComposerTextarea(){
  const input=$('text');
  if(!input)return;

  input.style.height='auto';
  const nextHeight=Math.min(120,input.scrollHeight);
  input.style.height=Math.max(48,nextHeight)+'px';

  const overflow=input.scrollHeight>120;
  input.style.overflowY=overflow?'auto':'hidden';

  // Keep the newest line/caret visible when the field reaches max height.
  if(overflow){
    requestAnimationFrame(()=>{
      input.scrollTop=input.scrollHeight
    })
  }else{
    input.scrollTop=0
  }
}

$('text').oninput=()=>{
  syncSend();
  saveCurrentDraft();
  handleTypingInput();
  resizeComposerTextarea()
};

$('attach').onclick=()=>$('fileInput').click();
$('removeFile').onclick=clearPending;
$('cancelReply').onclick=clearReplySource;

const ATTACHMENT_MAX_BYTES=20*1024*1024;
const IMAGE_OPTIMIZE_TRIGGER_BYTES=700*1024;
const IMAGE_OPTIMIZE_MAX_SIDE=1920;
const IMAGE_OPTIMIZE_QUALITY=.82;
const VIDEO_OPTIMIZE_TRIGGER_BYTES=5*1024*1024;
const VIDEO_OPTIMIZE_MAX_SIDE=1280;
const VIDEO_OPTIMIZE_MAX_DURATION=30;
const VIDEO_OPTIMIZE_BITS_PER_SECOND=1500000;

function canvasToBlob(canvas,type,quality){
  return new Promise(resolve=>canvas.toBlob(resolve,type,quality))
}

async function optimizeImageAttachment(file){
  const type=String(file?.type||'').toLowerCase();
  if(!type.startsWith('image/')||type==='image/gif'||file.size<IMAGE_OPTIMIZE_TRIGGER_BYTES)return file;
  let bitmap=null;
  try{
    bitmap=await createImageBitmap(file,{imageOrientation:'from-image'});
    const maxSide=Math.max(bitmap.width,bitmap.height);
    const scale=Math.min(1,IMAGE_OPTIMIZE_MAX_SIDE/maxSide);
    if(scale===1&&file.size<1.4*1024*1024)return file;
    const width=Math.max(1,Math.round(bitmap.width*scale));
    const height=Math.max(1,Math.round(bitmap.height*scale));
    const canvas=document.createElement('canvas');
    canvas.width=width;canvas.height=height;
    const ctx=canvas.getContext('2d',{alpha:false});
    if(!ctx)return file;
    ctx.drawImage(bitmap,0,0,width,height);
    const outputType=(type==='image/webp'&&HTMLCanvasElement.prototype.toBlob)?'image/webp':'image/jpeg';
    const blob=await canvasToBlob(canvas,outputType,IMAGE_OPTIMIZE_QUALITY);
    if(!blob||blob.size>=file.size*.92)return file;
    const base=String(file.name||'photo').replace(/\.[^.]+$/,'');
    const ext=outputType==='image/webp'?'.webp':'.jpg';
    return new File([blob],base+'-optimized'+ext,{type:outputType,lastModified:file.lastModified||Date.now()})
  }catch(err){
    console.warn('Image optimization skipped',err);
    return file
  }finally{
    try{bitmap?.close()}catch{}
  }
}

function supportedVideoRecorderMime(){
  if(typeof MediaRecorder==='undefined')return '';
  return ['video/webm;codecs=vp8,opus','video/webm;codecs=vp9,opus','video/webm']
    .find(type=>MediaRecorder.isTypeSupported?.(type))||''
}

async function optimizeVideoAttachment(file){
  const type=String(file?.type||'').toLowerCase();
  if(!type.startsWith('video/')||file.size<VIDEO_OPTIMIZE_TRIGGER_BYTES)return file;
  const recorderMime=supportedVideoRecorderMime();
  if(!recorderMime)return file;

  const video=document.createElement('video');
  video.preload='metadata';video.playsInline=true;video.muted=true;
  const objectUrl=URL.createObjectURL(file);video.src=objectUrl;
  let canvasStream=null,sourceStream=null,recorder=null,drawTimer=null,frameHandle=0;
  try{
    await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Видео не открылось')),8000);
      video.onloadedmetadata=()=>{clearTimeout(timer);resolve()};
      video.onerror=()=>{clearTimeout(timer);reject(new Error('Не удалось прочитать видео'))}
    });
    const duration=Number(video.duration)||0;
    if(!duration||duration>VIDEO_OPTIMIZE_MAX_DURATION)return file;
    const sourceWidth=Number(video.videoWidth)||0,sourceHeight=Number(video.videoHeight)||0;
    if(!sourceWidth||!sourceHeight)return file;
    const scale=Math.min(1,VIDEO_OPTIMIZE_MAX_SIDE/Math.max(sourceWidth,sourceHeight));
    const width=Math.max(2,Math.round(sourceWidth*scale/2)*2);
    const height=Math.max(2,Math.round(sourceHeight*scale/2)*2);
    const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
    const ctx=canvas.getContext('2d',{alpha:false});
    if(!ctx||typeof canvas.captureStream!=='function')return file;
    sourceStream=typeof video.captureStream==='function'?video.captureStream()
      :typeof video.mozCaptureStream==='function'?video.mozCaptureStream():null;
    if(!sourceStream)return file;
    canvasStream=canvas.captureStream(24);
    sourceStream.getAudioTracks().forEach(track=>{try{canvasStream.addTrack(track)}catch{}});
    const chunks=[];
    recorder=new MediaRecorder(canvasStream,{
      mimeType:recorderMime,
      videoBitsPerSecond:VIDEO_OPTIMIZE_BITS_PER_SECOND,
      audioBitsPerSecond:96000
    });
    recorder.ondataavailable=event=>{if(event.data?.size)chunks.push(event.data)};
    const stopped=new Promise((resolve,reject)=>{
      recorder.onstop=resolve;
      recorder.onerror=event=>reject(event.error||new Error('Ошибка сжатия видео'))
    });
    const draw=()=>{
      if(video.ended||video.paused)return;
      try{ctx.drawImage(video,0,0,width,height)}catch{}
      if(typeof video.requestVideoFrameCallback==='function')frameHandle=video.requestVideoFrameCallback(draw)
    };
    video.currentTime=0;await video.play();recorder.start(1000);
    if(typeof video.requestVideoFrameCallback==='function')frameHandle=video.requestVideoFrameCallback(draw);
    else drawTimer=setInterval(()=>{if(!video.paused&&!video.ended){try{ctx.drawImage(video,0,0,width,height)}catch{}}},42);
    await new Promise((resolve,reject)=>{
      video.onended=resolve;
      video.onerror=()=>reject(new Error('Ошибка воспроизведения видео'))
    });
    if(recorder.state!=='inactive')recorder.stop();
    await stopped;
    const blob=new Blob(chunks,{type:recorderMime});
    if(!blob.size||blob.size>=file.size*.92)return file;
    const base=String(file.name||'video').replace(/\.[^.]+$/,'');
    return new File([blob],base+'-optimized.webm',{type:'video/webm',lastModified:file.lastModified||Date.now()})
  }catch(err){
    console.warn('Video optimization skipped',err);
    return file
  }finally{
    if(drawTimer)clearInterval(drawTimer);
    try{if(frameHandle&&typeof video.cancelVideoFrameCallback==='function')video.cancelVideoFrameCallback(frameHandle)}catch{}
    try{if(recorder?.state&&recorder.state!=='inactive')recorder.stop()}catch{}
    try{canvasStream?.getTracks().forEach(track=>track.stop())}catch{}
    try{sourceStream?.getTracks().forEach(track=>track.stop())}catch{}
    try{video.pause()}catch{}
    video.removeAttribute('src');try{video.load()}catch{};URL.revokeObjectURL(objectUrl)
  }
}

async function optimizeAttachmentFile(file){
  if(!file)return file;
  const type=String(file.type||'').toLowerCase();
  if(type.startsWith('image/'))return optimizeImageAttachment(file);
  if(type.startsWith('video/'))return optimizeVideoAttachment(file);
  return file
}

$('fileInput').onchange=async()=>{
  const file=$('fileInput').files?.[0];if(!file)return;
  uploading=true;syncSend();$('pendingFileName').textContent='Подготовка: '+file.name;$('pendingFile').classList.remove('hidden');
  try{
    const uploadFile=await optimizeAttachmentFile(file);
    if(uploadFile.size>ATTACHMENT_MAX_BYTES)throw new Error('Файл больше 20 МБ');
    const saved=Math.max(0,file.size-uploadFile.size);
    $('pendingFileName').textContent=(saved>64*1024?'Сжато '+formatSize(file.size)+' → '+formatSize(uploadFile.size)+' · ':'Загрузка: ')+uploadFile.name;
    const form=new FormData();form.append('file',uploadFile);
    const r=await fetch('/api/uploads',{method:'POST',headers:{...authHeaders()},body:form});
    const data=await r.json();if(!r.ok)throw new Error(data?.detail||'Ошибка загрузки');
    pendingAttachment=data;$('pendingFileName').textContent='📎 '+data.name+' · '+formatSize(data.size)
  }catch(err){clearPending();alert(err.message)}
  finally{uploading=false;syncSend()}
};

$('composer').onsubmit=async e=>{
  e.preventDefault();
  const body=$('text').value.trim();
  if((!body&&!pendingAttachment)||!active||uploading)return;

  const target={type:active.type,id:Number(active.data.id)};
  const attachmentId=pendingAttachment?.id||null;
  const attachment=pendingAttachment?{...pendingAttachment}:null;
  const replyToMessageId=replySource?.id||null;
  const clientMessageId=makeClientMessageId();
  const payload={
    body,
    attachment_id:attachmentId,
    client_message_id:clientMessageId,
    reply_to_message_id:replyToMessageId
  };
  if(target.type==='user')payload.recipient_id=target.id;

  const entry=queueOutbox({
    id:clientMessageId,
    chat_type:target.type,
    chat_id:target.id,
    path:target.type==='user'
      ?'/api/messages'
      :'/api/groups/'+target.id+'/messages',
    payload,
    attachment,
    state:navigator.onLine?'sending':'waiting',
    queued_at:new Date().toISOString()
  });

  appendMessage(outboxMessage(entry,entry.state));
  clearComposerAfterQueued(body,attachmentId,replyToMessageId);

  try{
    await deliverOutboxEntry(entry)
  }catch(err){
    if(err?.queued){
      scheduleOutboxFlush();
      return
    }
    alert(err?.message||'Не удалось отправить сообщение')
  }
};

function urlBase64ToUint8Array(value){
  const padding='='.repeat((4-value.length%4)%4);
  const base64=(value+padding).replace(/-/g,'+').replace(/_/g,'/');
  const raw=atob(base64);
  return Uint8Array.from([...raw].map(ch=>ch.charCodeAt(0)))
}

async function ensurePushSubscription(registration){
  const config=await api('/api/push/public-key');
  if(!config.configured||!config.public_key)throw new Error('Push-уведомления пока не настроены на сервере');
  let sub=await registration.pushManager.getSubscription();
  if(!sub){
    sub=await registration.pushManager.subscribe({
      userVisibleOnly:true,
      applicationServerKey:urlBase64ToUint8Array(config.public_key)
    })
  }
  await api('/api/push/subscribe',{method:'POST',body:sub.toJSON()});
  setDrawerButton($('notifyBtn'),'✅','Уведомления','Вкл');
  $('notifyBtn').title='Уведомления включены — нажми для теста';
  return sub
}

function nativePushPlugin(){
  return window.Capacitor?.Plugins?.NativePush||null
}

async function clearNativeCallNotification(callId){
  if(!callId)return;
  try{await nativePushPlugin()?.clearCall?.({callId:String(callId)})}catch{}
}

async function maybeRequestFullScreenCalls(result){
  if(
    !result?.permission
    || result?.fullScreenAllowed!==false
    || localStorage.getItem('svoi_fullscreen_prompted')==='1'
  )return;
  localStorage.setItem('svoi_fullscreen_prompted','1');
  if(confirm('Разрешить «Свои» показывать входящие звонки поверх экрана блокировки?')){
    try{await nativePushPlugin()?.requestFullScreen?.()}catch{}
  }
}

async function ensureNativePushRegistration(){
  const plugin=nativePushPlugin();
  if(!plugin)return null;
  const result=await plugin.register();
  if(!result?.token)throw new Error('Android не вернул FCM-токен');
  await api('/api/push/android/register',{
    method:'POST',
    body:{token:result.token}
  });
  localStorage.setItem('svoi_fcm_token',result.token);
  if(result.permission){
    setDrawerButton($('notifyBtn'),'✅','Уведомления','Android');
    $('notifyBtn').title=result.fullScreenAllowed===false
      ?'Уведомления включены; полноэкранные звонки нужно разрешить'
      :'Системные Android-уведомления включены';
    await maybeRequestFullScreenCalls(result)
  }else{
    setDrawerButton($('notifyBtn'),'🔔','Уведомления','Разрешить');
    $('notifyBtn').title='Нажми и разреши уведомления Android'
  }
  return result
}

async function initPush(){
  const btn=$('notifyBtn');

  if(nativePushPlugin()){
    btn.classList.remove('hidden');
    try{
      await ensureNativePushRegistration()
    }catch(err){
      setDrawerButton(btn,'⚠️','Уведомления','Ошибка');
      btn.title=err.message||'Не удалось подключить Android-уведомления'
    }
    return
  }

  if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window)){
    btn.classList.add('hidden');return
  }
  const registration=await navigator.serviceWorker.register('/sw.js');
  try{await registration.update()}catch{}
  if(Notification.permission==='granted'){
    await ensurePushSubscription(registration)
  }else if(Notification.permission==='denied'){
    setDrawerButton(btn,'🔕','Уведомления','Запрещены');btn.title='Уведомления запрещены в настройках браузера'
  }else{
    setDrawerButton(btn,'🔔','Уведомления','Выкл');btn.title='Включить уведомления'
  }
}

$('notifyBtn').onclick=async()=>{
  try{
    if(nativePushPlugin()){
      const result=await ensureNativePushRegistration();
      if(!result?.permission){
        setTimeout(async()=>{
          try{
            const state=await nativePushPlugin()?.status?.();
            if(state?.permission){
              setDrawerButton($('notifyBtn'),'✅','Уведомления','Android');
              $('notifyBtn').title='Системные Android-уведомления включены'
            }
          }catch{}
        },1200)
      }
      return
    }

    if(!('Notification' in window))throw new Error('Этот браузер не поддерживает уведомления');
    if(Notification.permission==='denied')throw new Error('Разреши уведомления для этого сайта в настройках браузера');
    const permission=Notification.permission==='granted'
      ?'granted'
      :await Notification.requestPermission();
    if(permission!=='granted')return;
    const registration=await navigator.serviceWorker.ready;
    await ensurePushSubscription(registration);
    const result=await api('/api/push/test',{method:'POST'});
    setDrawerButton($('notifyBtn'),'✅','Уведомления','Вкл');
    $('notifyBtn').title='Тестовый push отправлен. Подписок: '+result.attempted
  }catch(err){
    setDrawerButton($('notifyBtn'),'⚠️','Уведомления','Ошибка');
    $('notifyBtn').title='Ошибка push';
    alert(err.message)
  }
};

async function removePushSubscription(){
  const fcmToken=localStorage.getItem('svoi_fcm_token')||'';
  if(fcmToken){
    try{
      await api('/api/push/android/unregister',{
        method:'POST',
        body:{token:fcmToken}
      })
    }catch{}
    localStorage.removeItem('svoi_fcm_token')
  }

  if(!('serviceWorker' in navigator))return;
  try{
    const registration=await navigator.serviceWorker.ready;
    const sub=await registration.pushManager.getSubscription();
    if(!sub)return;
    try{await api('/api/push/unsubscribe',{method:'POST',body:sub.toJSON()})}catch{}
    await sub.unsubscribe()
  }catch{}
}

function nativeAudioRoutePlugin(){
  return window.Capacitor?.Plugins?.NativeAudioRoute||null
}

async function applyNativeSpeakerMode(enabled){
  const plugin=nativeAudioRoutePlugin();
  if(!plugin)return false;
  const result=await queueNativeAudioRoute(()=>plugin.setSpeaker({enabled:!!enabled}));
  return result?.applied!==false
}

function queueNativeAudioRoute(action){
  const result=nativeAudioRouteChain.then(action);
  nativeAudioRouteChain=result.catch(()=>{});
  return result
}

async function resetNativeAudioRoute(){
  const generation=++audioRouteGeneration;
  const plugin=nativeAudioRoutePlugin();
  if(plugin){
    try{await queueNativeAudioRoute(()=>plugin.reset())}catch{}
  }
  if(generation!==audioRouteGeneration)return;
  callSpeakerMode=false;
  callAudioSinkId='';
  updateSpeakerButtons()
}

function nativeCallServicePlugin(){
  return window.Capacitor?.Plugins?.NativeCallService||null
}

async function startNativeCallService(name,video=false,group=false){
  const plugin=nativeCallServicePlugin();
  if(!plugin)return;
  try{
    await plugin.start({
      name:String(name||'Свои'),
      video:!!video,
      group:!!group
    })
  }catch{}
}

async function stopNativeCallService(){
  const plugin=nativeCallServicePlugin();
  if(!plugin)return;
  try{await plugin.stop()}catch{}
}

let liveCallDurationTimer=null;

function formatLiveCallDuration(ms){
  const total=Math.max(0,Math.floor(Number(ms||0)/1000));
  const hours=Math.floor(total/3600);
  const minutes=Math.floor((total%3600)/60);
  const seconds=total%60;
  const mm=String(minutes).padStart(2,'0');
  const ss=String(seconds).padStart(2,'0');
  return hours>0?hours+':'+mm+':'+ss:mm+':'+ss
}

function refreshLiveCallDurations(){
  const privateStarted=currentCall?.connectedAt||0;
  const privateBadge=$('callDuration');
  if(privateBadge){
    privateBadge.classList.toggle('hidden',!privateStarted);
    privateBadge.textContent=privateStarted
      ?formatLiveCallDuration(Date.now()-privateStarted)
      :'00:00'
  }

  const groupStarted=groupCallState?.connectedAt||0;
  const groupBadge=$('groupCallDuration');
  if(groupBadge){
    groupBadge.classList.toggle('hidden',!groupStarted);
    groupBadge.textContent=groupStarted
      ?formatLiveCallDuration(Date.now()-groupStarted)
      :'00:00'
  }

  updateMiniCallBar()
}

function ensureLiveCallDurationTimer(){
  refreshLiveCallDurations();
  if(liveCallDurationTimer)return;
  liveCallDurationTimer=setInterval(()=>{
    if(!currentCall?.connectedAt&&!groupCallState?.connectedAt){
      clearInterval(liveCallDurationTimer);
      liveCallDurationTimer=null;
      refreshLiveCallDurations();
      return
    }
    refreshLiveCallDurations()
  },1000)
}

function stopLiveCallDurationTimerIfIdle(){
  if(currentCall?.connectedAt||groupCallState?.connectedAt){
    refreshLiveCallDurations();
    return
  }
  if(liveCallDurationTimer){
    clearInterval(liveCallDurationTimer);
    liveCallDurationTimer=null
  }
  refreshLiveCallDurations()
}

function hideMiniCallBar(){
  minimizedCallKind=null;
  $('callMiniBar')?.classList.add('hidden');
  $('callOverlay')?.classList.remove('call-minimized');
  $('groupCallOverlay')?.classList.remove('call-minimized')
}

function showMiniCallBar(kind,name,status){
  minimizedCallKind=kind;
  $('miniCallName').textContent=name||'Звонок';
  $('miniCallStatus').textContent=status||'Звонок продолжается';
  $('callMiniBar').classList.remove('hidden')
}

function minimizePrivateCall(){
  if(!currentCall)return;
  $('callOverlay').classList.add('call-minimized');
  showMiniCallBar(
    'private',
    currentCall.peerName||$('callName').textContent||'Звонок',
    currentCall.answered?'Звонок активен':'Вызов…'
  )
}

function minimizeGroupCall(){
  if(!groupCallState)return;
  $('groupCallOverlay').classList.add('call-minimized');
  showMiniCallBar(
    'group',
    $('groupCallName').textContent||'Групповой звонок',
    $('groupCallStatus').textContent||'Звонок активен'
  );
  scheduleGroupRemoteVideoQualitySync()
}

function restoreMinimizedCall(){
  if(minimizedCallKind==='group'&&groupCallState){
    $('groupCallOverlay').classList.remove('call-minimized');
    $('callMiniBar').classList.add('hidden');
    minimizedCallKind=null;
    requestAnimationFrame(()=>scheduleGroupRemoteVideoQualitySync());
    return
  }
  if(minimizedCallKind==='private'&&currentCall){
    $('callOverlay').classList.remove('call-minimized');
    $('callMiniBar').classList.add('hidden');
    minimizedCallKind=null
  }
}

function updateMiniCallBar(){
  if(minimizedCallKind==='private'&&currentCall){
    $('miniCallName').textContent=currentCall.peerName||'Звонок';
    const duration=currentCall.connectedAt
      ?formatLiveCallDuration(Date.now()-currentCall.connectedAt)
      :'';
    $('miniCallStatus').textContent=currentCall.answered
      ?('Звонок активен'+(duration?' · '+duration:'')+(currentCall.remoteMuted?' · 🔇 собеседник':''))
      :'Вызов…'
  }else if(minimizedCallKind==='group'&&groupCallState){
    $('miniCallName').textContent=$('groupCallName').textContent||'Групповой звонок';
    const duration=groupCallState.connectedAt
      ?formatLiveCallDuration(Date.now()-groupCallState.connectedAt)
      :'';
    const status=$('groupCallStatus').textContent||'Звонок активен';
    $('miniCallStatus').textContent=status+(duration?' · '+duration:'')
  }
}

function audioOutputElements(){
  return [
    $('remoteVideo'),
    ...document.querySelectorAll('#groupCallGrid audio, #groupCallStrip audio')
  ].filter(Boolean)
}

async function applyAudioOutput(element){
  if(!element||typeof element.setSinkId!=='function')return false;
  try{
    await element.setSinkId(callAudioSinkId||'');
    return true
  }catch{
    return false
  }
}

async function applyAudioOutputToAll(){
  const items=audioOutputElements();
  const results=await Promise.all(items.map(el=>applyAudioOutput(el)));
  return results.some(Boolean)
}

function updateSpeakerButtons(){
  for(const id of ['speakerCall','groupSpeakerBtn']){
    const button=$(id);
    if(!button)continue;
    button.textContent=callSpeakerMode?'🔊':'🔈';
    button.classList.toggle('off',callSpeakerMode);
    button.title=callSpeakerMode
      ?'Громкая связь включена · нажми для авто-режима'
      :'Авто: Bluetooth при подключении · иначе разговорный динамик'
  }
  syncNativeProximity().catch(()=>{})
}

async function chooseSpeakerOutput(){
  if(audioOutputSwitching)return;
  audioOutputSwitching=true;
  const generation=audioRouteGeneration;
  for(const id of ['speakerCall','groupSpeakerBtn']){
    if($(id))$(id).disabled=true
  }
  try{
    const nativePlugin=nativeAudioRoutePlugin();

    if(nativePlugin){
      const next=!callSpeakerMode;
      try{
        const applied=await applyNativeSpeakerMode(next);
        if(generation!==audioRouteGeneration)return;
        if(!applied)throw new Error('Android не смог переключить аудиовыход');
        callSpeakerMode=next;
        callAudioSinkId='';
        updateSpeakerButtons();
        return
      }catch(err){
        if(generation!==audioRouteGeneration)return;
        alert(err?.message||'Не удалось переключить динамик');
        return
      }
    }

    const media=navigator.mediaDevices;
    const remote=$('remoteVideo');

    if(!remote||typeof remote.setSinkId!=='function'){
      alert('Этот браузер не поддерживает переключение аудиовыхода.');
      return
    }

    if(callSpeakerMode){
      callAudioSinkId='';
      callSpeakerMode=false;
      await applyAudioOutputToAll();
      updateSpeakerButtons();
      return
    }

    try{
      let device=null;

      try{
        const outputs=(await media.enumerateDevices()).filter(d=>d.kind==='audiooutput');
        device=outputs.find(d=>/speaker|loudspeaker|speakerphone|динамик|громк/i.test(d.label||''))
      }catch{}

      if(!device&&typeof media.selectAudioOutput==='function'){
        device=await media.selectAudioOutput()
      }

      if(!device?.deviceId){
        throw new Error('Аудиовыходом управляет система устройства')
      }

      if(generation!==audioRouteGeneration)return;
      callAudioSinkId=device.deviceId;
      callSpeakerMode=true;
      const applied=await applyAudioOutputToAll();
      if(generation!==audioRouteGeneration)return;
      if(!applied){
        callAudioSinkId='';
        callSpeakerMode=false;
        throw new Error('Не удалось переключить аудиовыход')
      }
      updateSpeakerButtons()
    }catch(err){
      if(generation!==audioRouteGeneration)return;
      if(err?.name==='NotAllowedError'){
        alert('Разреши выбор аудиовыхода для этого сайта')
      }else if(err?.name!=='AbortError'){
        alert(err?.message||'Не удалось включить громкую связь')
      }
    }
  }finally{
    audioOutputSwitching=false;
    for(const id of ['speakerCall','groupSpeakerBtn']){
      if($(id))$(id).disabled=false
    }
  }
}

function groupLayoutClass(count){
  if(count<=1)return 'layout-1';
  if(count===2)return 'layout-2';
  if(count<=4)return 'layout-3-4';
  if(count<=6)return 'layout-5-6';
  if(count<=9)return 'layout-7-9';
  return 'layout-10'
}

function applyGroupAutoLayout(count=null){
  const grid=$('groupCallGrid');
  if(!grid)return;
  const state=groupCallState;
  const participantCount=count==null
    ?(state?.room?1+state.room.remoteParticipants.size:grid.querySelectorAll('.group-tile').length)
    :Number(count);
  const normalizedCount=Math.max(1,participantCount||1);

  grid.classList.remove(
    'layout-1',
    'layout-2',
    'layout-3-4',
    'layout-5-6',
    'layout-7-9',
    'layout-10'
  );
  grid.classList.add(groupLayoutClass(normalizedCount));
  grid.dataset.participantCount=String(normalizedCount);

  requestAnimationFrame(()=>{
    if(groupCallState!==state)return;
    scheduleGroupRemoteVideoQualitySync();

    // Video calls use the same main-participant stage as private calls,
    // including calls with only one remote participant.
    const focusMinimum=state?.video?2:5;
    if(normalizedCount<focusMinimum){
      if(
        state?.groupFocusSource==='auto'
        &&grid.classList.contains('has-focus')
      ){
        restoreGroupGridLayout()
      }
      return
    }

    // Закреплённый участник всегда важнее автоматического говорящего.
    if(state?.pinnedIdentity||state?.groupGridRequested)return;

    const localIdentity=String(state?.room?.localParticipant?.identity||'');
    const activeRemote=(state?.pendingSpeakers||[]).find(participant=>{
      const identity=String(participant?.identity||'');
      return identity&&identity!==localIdentity
    });
    const fallbackRemote=state?.room
      ?[...state.room.remoteParticipants.values()][0]
      :null;
    const targetIdentity=String(
      activeRemote?.identity
      ||fallbackRemote?.identity
      ||localIdentity
      ||''
    );
    const target=targetIdentity?$(groupTileId(targetIdentity)):null;

    if(target?.isConnected&&!grid.classList.contains('has-focus')){
      showGroupFocusedTile(target,'auto')
    }
  })
}

function restoreGroupGridLayout(manual=false){
  const grid=$('groupCallGrid');
  const strip=$('groupCallStrip');
  if(!grid||!strip)return;

  for(const tile of [...strip.querySelectorAll('.group-tile')]){
    tile.classList.remove('focused');
    grid.append(tile)
  }
  grid.querySelectorAll('.group-tile.focused')
    .forEach(tile=>tile.classList.remove('focused'));
  grid.classList.remove('has-focus');
  $('groupCallOverlay')?.classList.remove('group-focused-mode');
  strip.classList.add('hidden');
  if(groupCallState){
    groupCallState.groupFocusSource='';
    groupCallState.groupGridRequested=manual
  }
  applyGroupAutoLayout();
  scheduleGroupRemoteVideoQualitySync()
}

function showGroupFocusedTile(tile,source='manual'){
  if(!tile?.isConnected)return;
  const grid=$('groupCallGrid');
  const strip=$('groupCallStrip');
  if(!grid||!strip)return;

  if(groupCallState){
    groupCallState.groupFocusSource=source;
    groupCallState.groupGridRequested=false
  }

  if(tile.classList.contains('focused')){
    grid.classList.add('has-focus');
    $('groupCallOverlay')?.classList.add('group-focused-mode');
    strip.classList.remove('hidden');
    return
  }

  const currentFocused=grid.querySelector('.group-tile.focused');
  if(currentFocused){
    currentFocused.classList.remove('focused');
    strip.append(currentFocused)
  }

  if(tile.parentElement===strip){
    grid.append(tile)
  }

  tile.classList.add('focused');
  grid.classList.add('has-focus');
  $('groupCallOverlay')?.classList.add('group-focused-mode');
  strip.classList.remove('hidden');

  for(const other of [...grid.querySelectorAll('.group-tile')]){
    if(other!==tile)strip.append(other)
  }

  requestAnimationFrame(()=>{
    strip.scrollLeft=0;
    scheduleGroupRemoteVideoQualitySync()
  })
}

function updateGroupPinnedUi(){
  const state=groupCallState;
  const pinned=String(state?.pinnedIdentity||'');

  document.querySelectorAll('.group-tile').forEach(tile=>{
    const isPinned=!!pinned&&String(tile.dataset.participantIdentity||'')===pinned;
    tile.classList.toggle('pinned',isPinned);
    tile.querySelector('.tile-pin-status')?.classList.toggle('hidden',!isPinned)
  });

  const button=$('groupCallProfilePin');
  if(button){
    const profileIdentity=String(groupCallProfileUser?.identity||'');
    const isPinned=!!pinned&&profileIdentity===pinned;
    button.textContent=isPinned?'📍 Открепить':'📌 Закрепить';
    button.classList.toggle('active',isPinned)
  }
}

function setGroupPinnedIdentity(identity=''){
  const state=groupCallState;
  if(!state)return;

  const next=String(identity||'');
  state.pinnedIdentity=next;

  if(state.speakerFocusTimer){
    clearTimeout(state.speakerFocusTimer);
    state.speakerFocusTimer=null
  }
  state.autoSpeakerIdentity='';

  updateGroupPinnedUi();

  if(next){
    const tile=$(groupTileId(next));
    if(tile?.isConnected)showGroupFocusedTile(tile,'pinned')
  }
}

function scheduleGroupSpeakerFocus(identity){
  const state=groupCallState;
  if(
    !state||state.pinnedIdentity||state.groupGridRequested
    ||state.groupFocusSource==='manual'
  )return;

  const participantCount=state.room?1+state.room.remoteParticipants.size:0;
  if(participantCount<(state.video?2:5))return;

  const next=String(identity||'');
  if(!next)return;

  const focused=$('groupCallGrid')?.querySelector('.group-tile.focused');
  if(String(focused?.dataset?.participantIdentity||'')===next){
    state.autoSpeakerIdentity=next;
    return
  }

  if(state.autoSpeakerIdentity===next&&state.speakerFocusTimer)return;

  if(state.speakerFocusTimer)clearTimeout(state.speakerFocusTimer);
  state.autoSpeakerIdentity=next;
  state.speakerFocusTimer=setTimeout(()=>{
    if(groupCallState!==state||state.pinnedIdentity)return;
    state.speakerFocusTimer=null;
    if(state.groupGridRequested||state.groupFocusSource==='manual')return;
    const tile=$(groupTileId(next));
    if(tile?.isConnected)showGroupFocusedTile(tile,'auto')
  },650)
}

function focusGroupTile(tile){
  if(!tile)return;
  const state=groupCallState;
  const identity=String(tile.dataset.participantIdentity||'');

  if(tile.classList.contains('focused')){
    if(state?.pinnedIdentity===identity)setGroupPinnedIdentity('');
    restoreGroupGridLayout(true);
    return
  }

  if(state?.pinnedIdentity&&state.pinnedIdentity!==identity){
    setGroupPinnedIdentity('')
  }
  showGroupFocusedTile(tile,'manual')
}

function groupParticipantUserId(identity){
  const match=String(identity||'').match(/^user-(\d+)$/);
  return match?Number(match[1]):0
}

function groupTileId(identity){
  return 'group-tile-'+String(identity).replace(/[^a-zA-Z0-9_-]/g,'_')
}

function groupVideoTargetForTile(tile){
  const minimized=minimizedCallKind==='group'
    ||$('groupCallOverlay')?.classList.contains('call-minimized');
  if(minimized){
    return {enabled:false,width:320,height:180,fps:12,label:'paused'}
  }

  const networkCap=groupCallState?.videoNetworkCap||'high';
  const strip=$('groupCallStrip');
  const width=Math.max(0,Number(tile?.getBoundingClientRect?.().width)||0);
  const identity=tile?.dataset?.participantIdentity||'';
  const active=!!groupCallState?.activeSpeakerIds?.has(String(identity));

  let target;
  if(tile?.classList.contains('focused')){
    target={enabled:true,width:1280,height:720,fps:24,label:'high'}
  }else if(tile&&strip&&tile.parentElement===strip){
    target={enabled:true,width:320,height:180,fps:15,label:'low'}
  }else if(width>=420){
    target={enabled:true,width:1280,height:720,fps:24,label:'high'}
  }else if(width>=190||active){
    target={enabled:true,width:640,height:360,fps:20,label:'medium'}
  }else{
    target={enabled:true,width:320,height:180,fps:15,label:'low'}
  }

  // Audio has priority. On a weak link keep the focused/active speaker
  // readable while aggressively shrinking background thumbnails.
  if(networkCap==='low'){
    if(tile?.classList.contains('focused')||active){
      return {enabled:true,width:640,height:360,fps:15,label:'medium'}
    }
    return {enabled:true,width:320,height:180,fps:10,label:'low'}
  }
  if(networkCap==='medium'){
    if(target.label==='high'){
      return {enabled:true,width:640,height:360,fps:18,label:'medium'}
    }
    if(target.label==='medium'){
      return {enabled:true,width:640,height:360,fps:18,label:'medium'}
    }
    return {enabled:true,width:320,height:180,fps:12,label:'low'}
  }
  return target
}

function normalizeLiveKitConnectionQuality(quality){
  const known=LivekitClient.ConnectionQuality||{};
  if(known.Excellent!=null&&quality===known.Excellent)return 'excellent';
  if(known.Good!=null&&quality===known.Good)return 'good';
  if(known.Poor!=null&&quality===known.Poor)return 'poor';
  if(known.Lost!=null&&quality===known.Lost)return 'poor';

  const raw=String(quality?.toString?.()??quality??'').toLowerCase();
  if(raw.includes('poor')||raw.includes('lost'))return 'poor';
  if(raw.includes('excellent'))return 'excellent';
  if(raw.includes('good'))return 'good';

  if(known.Excellent!=null&&raw===String(known.Excellent).toLowerCase())return 'excellent';
  if(known.Good!=null&&raw===String(known.Good).toLowerCase())return 'good';
  if(known.Poor!=null&&raw===String(known.Poor).toLowerCase())return 'poor';
  return 'unknown'
}

function setGroupConnectionQuality(level){
  const badge=$('groupConnectionQuality');
  if(!badge)return;
  const normalized=['excellent','good','poor'].includes(level)?level:'unknown';
  badge.classList.remove('good','medium','poor','checking');

  if(normalized==='excellent'){
    badge.classList.add('good');
    badge.textContent='Связь: хорошая'
  }else if(normalized==='good'){
    badge.classList.add('medium');
    badge.textContent='Связь: средняя'
  }else if(normalized==='poor'){
    badge.classList.add('poor');
    badge.textContent='Связь: плохая'
  }else{
    badge.classList.add('checking');
    badge.textContent='Связь: проверяем'
  }
}

function groupVideoNetworkCapRank(value){
  return value==='high'?2:(value==='medium'?1:0)
}

function groupVideoNetworkCapForQuality(quality,current='medium'){
  const normalized=normalizeLiveKitConnectionQuality(quality);
  if(normalized==='poor')return 'low';
  if(normalized==='good')return 'medium';
  if(normalized==='excellent')return 'high';
  return current||'medium'
}

function applyGroupVideoNetworkCap(state,next){
  if(!state||groupCallState!==state)return;
  state.pendingVideoNetworkCap='';
  if(state.videoNetworkCap===next)return;
  state.videoNetworkCap=next;
  state.lastVideoNetworkCapChangeAt=Date.now();
  scheduleGroupRemoteVideoQualitySync()
}

function updateGroupVideoNetworkCap(quality,participant){
  const state=groupCallState;
  if(!state?.room)return;

  // Use the local participant because it represents this device's own
  // connection to the LiveKit room.
  const localIdentity=String(state.room.localParticipant?.identity||'');
  const participantIdentity=String(participant?.identity||'');
  if(participantIdentity&&localIdentity&&participantIdentity!==localIdentity)return;

  const normalized=normalizeLiveKitConnectionQuality(quality);
  setGroupConnectionQuality(normalized);

  const current=state.videoNetworkCap||'medium';
  const next=groupVideoNetworkCapForQuality(normalized,current);
  if(next===current){
    state.pendingVideoNetworkCap='';
    if(state.videoNetworkCapTimer){
      clearTimeout(state.videoNetworkCapTimer);
      state.videoNetworkCapTimer=null
    }
    return
  }

  if(state.pendingVideoNetworkCap===next&&state.videoNetworkCapTimer)return;
  if(state.videoNetworkCapTimer)clearTimeout(state.videoNetworkCapTimer);
  state.pendingVideoNetworkCap=next;

  const degrading=groupVideoNetworkCapRank(next)<groupVideoNetworkCapRank(current);
  const delay=degrading
    ?(next==='low'?350:1200)
    :(next==='high'?6500:3500);

  state.videoNetworkCapTimer=setTimeout(()=>{
    state.videoNetworkCapTimer=null;
    if(groupCallState!==state||state.pendingVideoNetworkCap!==next)return;
    applyGroupVideoNetworkCap(state,next)
  },delay)
}

function applyGroupRemoteVideoTarget(publication,tile){
  if(!publication)return;
  const kind=publication.kind;
  if(kind!==LivekitClient.Track.Kind.Video&&kind!=='video')return;

  const target=groupVideoTargetForTile(tile);
  const previous=publication.__svoiVideoTarget||'';
  const signature=[
    target.enabled?1:0,
    target.width,
    target.height,
    target.fps
  ].join(':');
  if(previous===signature)return;

  try{
    if(typeof publication.setEnabled==='function'){
      publication.setEnabled(target.enabled)
    }
    if(target.enabled&&typeof publication.setVideoDimensions==='function'){
      publication.setVideoDimensions({
        width:target.width,
        height:target.height
      })
    }
    if(target.enabled&&typeof publication.setVideoFPS==='function'){
      publication.setVideoFPS(target.fps)
    }
    publication.__svoiVideoTarget=signature
  }catch{}
}

function syncGroupRemoteVideoQuality(){
  const state=groupCallState;
  if(!state?.room)return;

  for(const participant of state.room.remoteParticipants.values()){
    const tile=$(groupTileId(participant.identity));
    for(const publication of participant.videoTrackPublications.values()){
      applyGroupRemoteVideoTarget(publication,tile)
    }
  }
}

function scheduleGroupRemoteVideoQualitySync(){
  const state=groupCallState;
  if(!state)return;
  if(state.videoQualityFrame)return;
  state.videoQualityFrame=requestAnimationFrame(()=>{
    if(groupCallState!==state)return;
    state.videoQualityFrame=0;
    syncGroupRemoteVideoQuality()
  })
}

function groupParticipantMicMuted(participant,isLocal=false){
  if(!participant)return false;
  if(isLocal&&groupCallState?.room?.localParticipant===participant){
    return !!groupCallState.muted
  }

  const publications=[...(participant.audioTrackPublications?.values?.()||[])];
  if(!publications.length)return false;

  const microphonePublications=publications.filter(publication=>{
    const source=publication?.source;
    return !source
      ||source===LivekitClient.Track.Source.Microphone
      ||String(source).toLowerCase().includes('microphone')
  });
  const relevant=microphonePublications.length
    ?microphonePublications
    :publications;
  return relevant.length>0&&relevant.every(publication=>!!publication.isMuted)
}

function updateGroupMicBadge(participant,isLocal=false,explicitMuted=null){
  if(!participant)return;
  const tile=$(groupTileId(participant.identity||'local'));
  if(!tile)return;

  let badge=tile.querySelector('.tile-mic-status');
  if(!badge){
    badge=document.createElement('div');
    badge.className='tile-mic-status hidden';
    badge.setAttribute('aria-label','Микрофон выключен');
    badge.title='Микрофон выключен';
    badge.textContent='🔇';
    tile.append(badge)
  }

  const muted=explicitMuted==null
    ?groupParticipantMicMuted(participant,isLocal)
    :!!explicitMuted;
  badge.classList.toggle('hidden',!muted);
  tile.classList.toggle('mic-muted',muted)
}

function isGroupMicrophonePublication(publication){
  if(!publication)return false;
  const kind=publication.kind;
  if(kind!==LivekitClient.Track.Kind.Audio&&kind!=='audio')return false;
  const source=publication.source;
  return !source
    ||source===LivekitClient.Track.Source.Microphone
    ||String(source).toLowerCase().includes('microphone')
}

function groupQualityFromPing(value){
  const ms=Number(value);
  if(!Number.isFinite(ms)||ms<0)return 'unknown';
  if(ms<=150)return 'good';
  if(ms<=350)return 'medium';
  return 'poor'
}

function groupQualityRank(value){
  return value==='poor'?3:(value==='medium'?2:(value==='good'?1:0))
}

function effectiveGroupParticipantQuality(identity){
  const state=groupCallState;
  if(!state)return 'unknown';

  const id=String(identity||'');
  const livekit=state.participantQualities?.get?.(id)||'unknown';
  const ping=state.participantPings?.get?.(id);
  const byPing=groupQualityFromPing(ping);

  if(groupQualityRank(livekit)>=groupQualityRank(byPing)){
    return livekit
  }
  return byPing
}

function updateGroupParticipantQuality(identity,quality=null){
  const state=groupCallState;
  const id=String(identity||'');
  if(!id)return;

  if(state&&quality!=null){
    const normalized=normalizeLiveKitConnectionQuality(quality);
    const mapped=normalized==='excellent'
      ?'good'
      :(normalized==='good'?'medium':(normalized==='poor'?'poor':'unknown'));
    state.participantQualities?.set?.(id,mapped)
  }

  const tile=$(groupTileId(id));
  if(!tile)return;

  let badge=tile.querySelector('.tile-network-quality');
  if(!badge){
    badge=document.createElement('div');
    badge.className='tile-network-quality';
    tile.append(badge)
  }

  const level=effectiveGroupParticipantQuality(id);
  const pingBadge=tile.querySelector('.tile-ping-status');
  if(pingBadge){
    pingBadge.classList.remove('good','medium','poor','checking')
  }
  if(level==='good'){
    badge.textContent='🟢';
    badge.title='Связь хорошая';
    pingBadge?.classList.add('good')
  }else if(level==='medium'){
    badge.textContent='🟡';
    badge.title='Связь средняя';
    pingBadge?.classList.add('medium')
  }else if(level==='poor'){
    badge.textContent='🔴';
    badge.title='Связь плохая';
    pingBadge?.classList.add('poor')
  }else{
    badge.textContent='⚪';
    badge.title='Качество связи определяется';
    pingBadge?.classList.add('checking')
  }
}

function updateGroupParticipantPing(identity,value){
  const tile=$(groupTileId(identity));
  if(!tile)return;
  let badge=tile.querySelector('.tile-ping-status');
  if(!badge){
    badge=document.createElement('div');
    badge.className='tile-ping-status';
    badge.textContent='Пинг —';
    tile.append(badge)
  }

  const ms=Number(value);
  if(Number.isFinite(ms)&&ms>=0){
    const rounded=Math.max(1,Math.round(ms));
    const level=effectiveGroupParticipantQuality(identity);
    const icon=level==='good'?'🟢':(level==='medium'?'🟡':(level==='poor'?'🔴':'⚪'));
    badge.textContent=icon+' '+rounded+' мс';
    badge.title='Пинг: '+rounded+' мс · '+(
      level==='good'?'хорошая связь':
      (level==='medium'?'средняя связь':
      (level==='poor'?'плохая связь':'качество определяется'))
    )
  }else{
    badge.textContent='⚪ — мс';
    badge.title='Пинг ещё не измерен'
  }
  updateGroupParticipantQuality(identity)
}

function groupAveragePing(state){
  const values=[...(state?.participantPings?.values?.()||[])]
    .map(Number)
    .filter(value=>Number.isFinite(value)&&value>=0);
  return values.length?values.reduce((sum,value)=>sum+value,0)/values.length:null
}

async function reportGroupCallQuality(state,endReason='',force=false){
  if(!state?.qualityKey)return;
  const now=Date.now();
  if(!force&&now-(state.lastQualityReportAt||0)<9500)return;
  state.lastQualityReportAt=now;
  const localQuality=normalizeLiveKitConnectionQuality(
    state.room?.localParticipant?.connectionQuality
  );
  await api('/api/calls/quality',{
    method:'POST',
    body:{
      call_key:state.qualityKey,
      call_type:state.kind==='link'?'link':'group',
      group_id:state.groupId||null,
      rtt_ms:groupAveragePing(state),
      connection_quality:localQuality==='unknown'?null:localQuality,
      end_reason:endReason||null
    }
  })
}

function startGroupQualityMonitoring(state=groupCallState){
  if(!state)return;
  if(state.qualityTimer)clearInterval(state.qualityTimer);
  reportGroupCallQuality(state).catch(()=>{});
  state.qualityTimer=setInterval(()=>{
    if(groupCallState===state)reportGroupCallQuality(state).catch(()=>{})
  },10000)
}

function stopGroupQualityMonitoring(state=groupCallState){
  if(state?.qualityTimer){
    clearInterval(state.qualityTimer);
    state.qualityTimer=null
  }
}

function stopGroupPingMonitoring(state=groupCallState){
  if(!state)return;
  if(state.pingTimer){
    clearInterval(state.pingTimer);
    state.pingTimer=null
  }
  if(state.pingPending?.clear)state.pingPending.clear()
}

function sendGroupPingProbe(state=groupCallState){
  if(!state||groupCallState!==state||state.manualLeave)return;
  if(!socket||socket.readyState!==WebSocket.OPEN)return;

  const nonce=Date.now()+'-'+(++state.pingSeq);
  state.pingPending.set(nonce,performance.now());

  while(state.pingPending.size>8){
    const first=state.pingPending.keys().next().value;
    state.pingPending.delete(first)
  }

  try{
    wsSend({
      type:'group_ping_probe',
      nonce
    })
  }catch{}
}

function startGroupPingMonitoring(state=groupCallState){
  if(!state||groupCallState!==state)return;
  stopGroupPingMonitoring(state);
  state.pingPending=new Map();
  state.pingSeq=0;

  const localIdentity=String(state.room?.localParticipant?.identity||'');
  if(localIdentity)updateGroupParticipantPing(localIdentity,null);

  sendGroupPingProbe(state);
  state.pingTimer=setInterval(()=>{
    sendGroupPingProbe(state)
  },4000)
}

function handleGroupPingPong(data){
  const state=groupCallState;
  if(!state?.pingPending)return;
  const nonce=String(data?.nonce||'');
  const started=state.pingPending.get(nonce);
  if(started==null)return;
  state.pingPending.delete(nonce);

  const ping=Math.max(1,Math.round(performance.now()-started));
  const identity=String(state.room?.localParticipant?.identity||'');
  if(identity){
    state.participantPings.set(identity,ping);
    updateGroupParticipantPing(identity,ping)
  }

  try{
    wsSend({
      type:'group_call_ping',
      group_id:state.groupId,
      ping_ms:ping
    })
  }catch{}
}

async function openGroupCallProfile(participant,tile){
  const state=groupCallState;
  if(!state||!participant)return;

  const identity=String(participant.identity||'');
  const userId=groupParticipantUserId(identity);
  let user=null;

  if(userId===Number(me?.id)){
    user={...me,id:Number(me.id),online:true}
  }else{
    user=users.find(item=>Number(item.id)===userId)||null
  }

  if(state.kind==='group'){
    try{
      const members=await api('/api/groups/'+state.groupId+'/members');
      const member=members?.members?.find(item=>Number(item.id)===userId);
      if(member)user={...(user||{}),...member}
    }catch{}
  }

  if(!user){
    user={
      id:userId,
      display_name:participant.name||identity||'Участник',
      username:'',
      online:true
    }
  }

  groupCallProfileUser={user,participant,identity,tile};

  const avatar=$('groupCallProfileAvatar');
  avatar.replaceChildren();
  if(user.avatar_url){
    const img=document.createElement('img');
    img.src=user.avatar_url;
    img.alt=user.display_name||'Участник';
    avatar.append(img)
  }else{
    avatar.textContent=initials(user.display_name||participant.name||identity)
  }

  $('groupCallProfileName').textContent=
    (user.display_name||participant.name||identity)
    +(userId===Number(me?.id)?' · вы':'');
  $('groupCallProfileTag').textContent=user.username?'@'+user.username:'';
  $('groupCallProfileStatus').textContent=userId===Number(me?.id)
    ?'Это вы'
    :(user.online?'● в сети':formatLastSeen(user));

  const muted=tile?.querySelector('.tile-mic-status')&&!tile.querySelector('.tile-mic-status').classList.contains('hidden');
  $('groupCallProfileMic').textContent=muted?'🔇 Микрофон выключен':'🎤 Микрофон включён';

  const ping=state.participantPings?.get?.(identity);
  $('groupCallProfilePing').textContent=Number.isFinite(Number(ping))
    ?'Пинг '+Math.round(Number(ping))+' мс'
    :'Пинг —';

  const quality=effectiveGroupParticipantQuality(identity);
  $('groupCallProfileQuality').textContent=quality==='good'
    ?'🟢 Хорошая связь'
    :(quality==='medium'
      ?'🟡 Средняя связь'
      :(quality==='poor'?'🔴 Плохая связь':'⚪ Связь'));

  $('groupCallProfileMessage').classList.toggle(
    'hidden',
    userId===Number(me?.id)||!userId
  );
  $('groupCallProfileMute').classList.toggle(
    'hidden',
    !state.isAdmin||userId===Number(me?.id)||!userId
  );

  updateGroupPinnedUi();
  $('groupCallProfileDialog').showModal()
}

function ensureGroupTile(participant,isLocal=false){
  const identity=participant.identity||'local';
  const id=groupTileId(identity);
  let tile=$(id);
  if(tile)return tile;

  tile=document.createElement('div');
  tile.id=id;tile.className='group-tile';
  tile.dataset.participantIdentity=String(identity);
  const avatar=document.createElement('div');avatar.className='tile-avatar';avatar.textContent=initials(participant.name||identity);
  const name=document.createElement('div');name.className='tile-name';name.textContent=(participant.name||identity)+(isLocal?' · вы':'');
  name.title='Открыть профиль участника';
  name.addEventListener('click',event=>{
    event.preventDefault();
    event.stopPropagation();
    openGroupCallProfile(participant,tile).catch(()=>{})
  });
  const mic=document.createElement('div');
  mic.className='tile-mic-status hidden';
  mic.setAttribute('aria-label','Микрофон выключен');
  mic.title='Микрофон выключен';
  mic.textContent='🔇';
  const speaking=document.createElement('div');
  speaking.className='tile-speaking-status hidden';
  speaking.setAttribute('aria-label','Сейчас говорит');
  speaking.textContent='🎙 Говорит';
  const ping=document.createElement('div');
  ping.className='tile-ping-status';
  ping.textContent='Пинг —';
  ping.title='Пинг ещё не измерен';
  const quality=document.createElement('div');
  quality.className='tile-network-quality';
  quality.textContent='⚪';
  quality.title='Качество связи определяется';

  const participantUserId=groupParticipantUserId(identity);
  let adminMute=null;
  if(
    !isLocal
    &&groupCallState?.isAdmin
    &&participantUserId>0
    &&participantUserId!==Number(me?.id)
  ){
    adminMute=document.createElement('button');
    adminMute.type='button';
    adminMute.className='tile-admin-mute';
    adminMute.textContent='🔇';
    adminMute.title='Заглушить участника';
    adminMute.setAttribute('aria-label','Заглушить участника');
    adminMute.onclick=event=>{
      event.stopPropagation();
      if(!groupCallState||!groupCallState.isAdmin)return;
      adminMute.disabled=true;
      try{
        wsSend({
          type:'group_force_mute',
          group_id:groupCallState.groupId,
          target_user_id:participantUserId
        })
      }catch(err){
        adminMute.disabled=false;
        alert(err.message||'Не удалось заглушить участника')
      }
      setTimeout(()=>{
        if(adminMute?.isConnected)adminMute.disabled=false
      },1200)
    }
  }

  const pin=document.createElement('div');
  pin.className='tile-pin-status hidden';
  pin.textContent='📌';
  pin.title='Участник закреплён';

  tile.append(avatar,name,mic,speaking,ping,quality,pin);
  if(adminMute)tile.append(adminMute);
  tile.title='Нажмите, чтобы увеличить участника';
  tile.addEventListener('click',event=>{
    if(event.target.closest('button'))return;
    focusGroupTile(tile)
  });
  const grid=$('groupCallGrid');
  const strip=$('groupCallStrip');
  if(grid?.classList.contains('has-focus')&&strip){
    strip.append(tile);
    strip.classList.remove('hidden')
  }else{
    grid?.append(tile)
  }
  updateGroupMicBadge(participant,isLocal);
  const knownPing=groupCallState?.participantPings?.get?.(String(identity));
  updateGroupParticipantPing(identity,knownPing??null);
  updateGroupParticipantQuality(identity,participant.connectionQuality);
  updateGroupPinnedUi();
  return tile
}

function updatePrivateLocalMirror(){
  const video=$('localVideo');
  if(!video)return;
  const shouldMirror=cameraFacing==='user';
  video.classList.toggle('mirrored',shouldMirror)
}

function attachGroupTrack(track,participant,isLocal=false,publication=null){
  const tile=ensureGroupTile(participant,isLocal);
  if(track.kind===LivekitClient.Track.Kind.Video){
    tile.querySelector('.tile-avatar')?.classList.add('hidden');
    const name=tile.querySelector('.tile-name');
    if(name){
      name.textContent=(participant.name||participant.identity||'Участник')
        +(isLocal?' · вы':'')
    }
    for(const old of tile.querySelectorAll('video')){
      try{old.pause()}catch{}
      old.remove()
    }
    const element=track.attach();
    element.autoplay=true;element.playsInline=true;
    if(isLocal){
      element.muted=true;
      const frontCamera=(groupCallState?.facing||'user')==='user';
      element.classList.toggle('local-mirrored',frontCamera)
    }
    tile.prepend(element);
    if(!isLocal&&publication){
      requestAnimationFrame(()=>{
        applyGroupRemoteVideoTarget(publication,tile)
      })
    }
  }else if(track.kind===LivekitClient.Track.Kind.Audio){
    // После реконнекта LiveKit может быстро переиздать аудиотрек.
    // Оставляем в плитке только один audio-элемент, иначе возможны эхо
    // и двойное воспроизведение одного участника.
    for(const old of tile.querySelectorAll('audio')){
      try{old.pause()}catch{}
      old.remove()
    }
    const element=track.attach();
    element.autoplay=true;element.playsInline=true;element.style.display='none';
    tile.append(element);
    applyAudioOutput(element).catch(()=>{});
    updateGroupMicBadge(participant,isLocal,!!publication?.isMuted)
  }
}

function detachGroupTrack(track,participant=null,publication=null){
  try{track.detach().forEach(el=>el.remove())}catch{}
  if(!participant)return;
  const tile=$(groupTileId(participant.identity));
  if(!tile)return;
  if(track.kind===LivekitClient.Track.Kind.Video){
    const stillHasVideo=!!tile.querySelector('video');
    tile.querySelector('.tile-avatar')?.classList.toggle('hidden',stillHasVideo);
    if(!stillHasVideo){
      const name=tile.querySelector('.tile-name');
      if(name)name.textContent=(participant.name||participant.identity||'Участник')
    }
  }
}

function refreshGroupCount(){
  const state=groupCallState;
  if(!state?.room)return;
  const count=1+state.room.remoteParticipants.size;
  const changed=state.lastParticipantCount!==count;
  state.lastParticipantCount=count;
  $('groupCallStatus').textContent=count+' / 10 участников';
  if(changed){
    applyGroupAutoLayout(count)
  }
  updateMiniCallBar()
}

function updateGroupActiveSpeakers(speakers=[]){
  const state=groupCallState;
  if(!state)return;
  state.pendingSpeakers=speakers.slice(0,3);
  if(state.speakerFrame)return;
  state.speakerFrame=requestAnimationFrame(()=>{
    if(groupCallState!==state)return;
    state.speakerFrame=0;
    const next=new Set(
      (state.pendingSpeakers||[]).map(p=>String(p.identity||''))
    );
    const previous=state.activeSpeakerIds||new Set();

    for(const identity of previous){
      if(!next.has(identity)){
        const tile=$(groupTileId(identity));
        tile?.classList.remove('speaking');
        tile?.querySelector('.tile-speaking-status')?.classList.add('hidden')
      }
    }
    for(const identity of next){
      if(!previous.has(identity)){
        const tile=$(groupTileId(identity));
        tile?.classList.add('speaking');
        tile?.querySelector('.tile-speaking-status')?.classList.remove('hidden')
      }
    }
    state.activeSpeakerIds=next;

    const localIdentity=String(state.room?.localParticipant?.identity||'');
    const primaryRemote=(state.pendingSpeakers||[]).find(participant=>{
      const identity=String(participant?.identity||'');
      return identity&&identity!==localIdentity
    });

    if(!state.pinnedIdentity&&primaryRemote){
      scheduleGroupSpeakerFocus(primaryRemote.identity)
    }else if(!primaryRemote&&state.speakerFocusTimer){
      clearTimeout(state.speakerFocusTimer);
      state.speakerFocusTimer=null;
      state.autoSpeakerIdentity=''
    }

    scheduleGroupRemoteVideoQualitySync()
  })
}

function setGroupCallStatus(text){
  if(groupCallState)$('groupCallStatus').textContent=text
}

async function recoverGroupCall(state){
  if(!state||groupCallState!==state||state.manualLeave||state.recovering)return;
  state.recovering=true;
  state.reconnectAttempts=(state.reconnectAttempts||0)+1;

  if(state.reconnectAttempts>5){
    setGroupCallStatus('Не удалось восстановить соединение');
    state.recovering=false;
    return
  }

  const delay=Math.min(8000,1500*state.reconnectAttempts);
  setGroupCallStatus('Восстанавливаем соединение…');
  await new Promise(resolve=>setTimeout(resolve,delay));

  if(groupCallState!==state||state.manualLeave){
    state.recovering=false;
    return
  }

  if(!navigator.onLine){
    state.recovering=false;
    setGroupCallStatus('Нет интернета · ждём сеть');
    return
  }

  try{
    const tokenEndpoint=state.kind==='link'
      ?'/api/call-links/'+encodeURIComponent(state.inviteToken)+'/token'
      :'/api/groups/'+state.groupId+'/call-token';
    const credentials=await api(tokenEndpoint,{
      method:'POST',
      body:{video:!state.cameraOff,invite:false}
    });

    if(groupCallState!==state||state.manualLeave){
      state.recovering=false;
      return
    }

    await state.room.connect(
      credentials.server_url,
      credentials.participant_token
    );

    if(groupCallState!==state||state.manualLeave){
      state.recovering=false;
      try{state.room.disconnect()}catch{}
      return
    }

    await state.room.localParticipant.setMicrophoneEnabled(!state.muted);
    updateGroupMicBadge(
      state.room.localParticipant,
      true,
      state.muted
    );
    await state.room.localParticipant.setCameraEnabled(!state.cameraOff);
    if(!state.cameraOff){
      let track=null;
      for(const pub of state.room.localParticipant.videoTrackPublications.values()){
        if(pub.track){track=pub.track;break}
      }
      if(track&&state.facing){
        try{await track.restartTrack({facingMode:state.facing})}catch{}
      }
    }

    state.reconnectAttempts=0;
    state.needsRecovery=false;
    state.recovering=false;
    await renderGroupLocalTracks();
    refreshGroupCount();
    requestAnimationFrame(()=>scheduleGroupRemoteVideoQualitySync())
  }catch(err){
    state.recovering=false;
    state.needsRecovery=true;
    recoverGroupCall(state).catch(()=>{})
  }
}

async function renderGroupLocalTracks(){
  if(!groupCallState?.room)return;
  const p=groupCallState.room.localParticipant;
  const tile=ensureGroupTile(p,true);
  const publications=[...p.videoTrackPublications.values()]
    .filter(pub=>pub.track);
  const pub=publications[0]||null;
  if(pub?.track)attachGroupTrack(pub.track,p,true,pub);
  const hasVideo=!!pub?.track;
  tile.querySelector('.tile-avatar')?.classList.toggle('hidden',hasVideo);
  if(!hasVideo){
    const name=tile.querySelector('.tile-name');
    if(name)name.textContent=(p.name||p.identity||'Вы')+' · вы'
  }
  updateGroupMicBadge(p,true);
  window.SvoiAdminMasks?.scheduleGroup();
}

let groupCallJoining=false;

async function joinGroupCall(groupId,video=false,invite=false,linkedToken='',invitationVideo=null){
  if(groupCallJoining||groupCallState)return;
  if(currentCall||pendingCall){
    alert('Сначала заверши текущий личный звонок');return
  }
  if(!window.LivekitClient){
    alert('Модуль групповых звонков не загрузился');return
  }

  groupCallJoining=true;
  let room=null;
  try{
    const tokenEndpoint=linkedToken
      ?'/api/call-links/'+encodeURIComponent(linkedToken)+'/token'
      :'/api/groups/'+groupId+'/call-token';
    const credentials=await api(tokenEndpoint,{
      method:'POST',
      body:{video:!!video,invite:!!invite}
    });
    if(currentCall||pendingCall){
      alert('Сначала заверши текущий личный звонок');return false
    }
    const callVideo=invitationVideo==null?(linkedToken?!!credentials.video:!!video):!!invitationVideo;

    room=new LivekitClient.Room({
      adaptiveStream:true,
      dynacast:true,
      disconnectOnPageLeave:false,
      audioCaptureDefaults:{
        echoCancellation:true,
        noiseSuppression:true,
        autoGainControl:true
      },
      videoCaptureDefaults:{
        resolution:LivekitClient.VideoPresets.h720.resolution,
        frameRate:24
      },
      publishDefaults:{
        audioPreset:LivekitClient.AudioPresets.speech,
        dtx:true,
        red:true,
        simulcast:true,
        degradationPreference:'maintain-framerate',
        videoEncoding:LivekitClient.VideoPresets.h720.encoding,
        videoSimulcastLayers:[
          LivekitClient.VideoPresets.h180,
          LivekitClient.VideoPresets.h360
        ]
      }
    });

    // Начинаем подготовку WebRTC-соединения сразу, пока строится интерфейс.
    // Это сокращает задержку до фактического room.connect().
    try{room.prepareConnection(credentials.server_url,credentials.participant_token)}catch{}

    groupCallState={
      room,
      groupId:linkedToken?null:groupId,
      kind:linkedToken?'link':'group',
      inviteToken:linkedToken||'',
      video:callVideo,
      isAdmin:!!credentials.is_admin,
      connectedAt:0,
      muted:false,
      cameraOff:!callVideo,
      facing:'user',
      reconnectAttempts:0,
      recovering:false,
      needsRecovery:false,
      manualLeave:false,
      lastParticipantCount:-1,
      speakerFrame:0,
      speakerFocusTimer:null,
      autoSpeakerIdentity:'',
      pinnedIdentity:'',
      groupFocusSource:'',
      groupGridRequested:false,
      videoQualityFrame:0,
      videoNetworkCap:'high',
      pendingVideoNetworkCap:'',
      videoNetworkCapTimer:null,
      lastVideoNetworkCapChangeAt:0,
      pendingSpeakers:[],
      activeSpeakerIds:new Set(),
      participantQualities:new Map(),
      participantPings:new Map(),
      pingPending:new Map(),
      pingSeq:0,
      pingTimer:null,
      qualityKey:(linkedToken?'link':'group')+':'+String(credentials.room_name||groupId||'call')+':'+(crypto.randomUUID?crypto.randomUUID():(Date.now()+'-'+Math.random().toString(16).slice(2))),
      qualityTimer:null,
      lastQualityReportAt:0
    };

    $('groupCallGrid').replaceChildren();
    $('groupCallStrip').replaceChildren();
    $('groupCallStrip').classList.add('hidden');
    $('groupCallGrid').classList.remove(
      'has-focus',
      'layout-1',
      'layout-2',
      'layout-3-4',
      'layout-5-6',
      'layout-7-9',
      'layout-10'
    );
    $('groupCallGrid').classList.add('layout-1');
    $('groupCallName').textContent=credentials.group_name;
    $('groupCallStatus').textContent='Подключение…';
    $('groupInviteLinkWrap').classList.remove('hidden');
    $('groupCallDuration').textContent='00:00';
    $('groupCallDuration').classList.add('hidden');
    $('groupCallOverlay').classList.remove('group-focused-mode');
    $('groupCallOverlay').classList.remove('hidden');
    applyGroupAutoLayout();
    updateSpeakerButtons();
    $('groupCameraBtn').classList.toggle('off',!callVideo);
    $('groupCameraBtn').textContent=callVideo?'📷':'🎥';
    $('groupCameraBtn').title=callVideo
      ?'Переключить в аудиорежим'
      :'Переключить в видеорежим';
    $('groupSwitchCameraBtn').classList.toggle('hidden',!callVideo);

    room
      .on(LivekitClient.RoomEvent.TrackSubscribed,(track,publication,participant)=>{
        attachGroupTrack(track,participant,false,publication)
      })
      .on(LivekitClient.RoomEvent.TrackUnsubscribed,(track,publication,participant)=>{
        detachGroupTrack(track,participant,publication)
      })
      .on(LivekitClient.RoomEvent.TrackMuted,(publication,participant)=>{
        if(isGroupMicrophonePublication(publication)){
          updateGroupMicBadge(participant,false,true)
        }
      })
      .on(LivekitClient.RoomEvent.TrackUnmuted,(publication,participant)=>{
        if(isGroupMicrophonePublication(publication)){
          updateGroupMicBadge(participant,false,false)
        }
      })
      .on(LivekitClient.RoomEvent.ParticipantConnected,(participant)=>{
        ensureGroupTile(participant,false);
        updateGroupMicBadge(participant,false);
        updateGroupParticipantQuality(participant.identity,participant.connectionQuality);
        refreshGroupCount();
      })
      .on(LivekitClient.RoomEvent.ParticipantDisconnected,(participant)=>{
        const identity=String(participant.identity||'');
        const leavingTile=$(groupTileId(identity));
        const wasFocused=!!leavingTile?.classList.contains('focused');
        const wasPinned=groupCallState?.pinnedIdentity===identity;
        if(wasPinned)setGroupPinnedIdentity('');
        leavingTile?.remove();
        if(wasFocused)restoreGroupGridLayout();
        refreshGroupCount()
      })
      .on(LivekitClient.RoomEvent.ActiveSpeakersChanged,(speakers)=>{
        updateGroupActiveSpeakers(speakers)
      })
      .on(LivekitClient.RoomEvent.ConnectionQualityChanged,(quality,participant)=>{
        updateGroupParticipantQuality(participant?.identity,quality);
        updateGroupVideoNetworkCap(quality,participant)
      })
      .on(LivekitClient.RoomEvent.Reconnecting,()=>{
        if(groupCallState?.room===room){
          groupCallState.needsRecovery=false;
          if(groupCallState.videoNetworkCapTimer){
            clearTimeout(groupCallState.videoNetworkCapTimer);
            groupCallState.videoNetworkCapTimer=null
          }
          groupCallState.pendingVideoNetworkCap='';
          groupCallState.videoNetworkCap='low';
          setGroupConnectionQuality('poor');
          scheduleGroupRemoteVideoQualitySync();
          setGroupCallStatus('Восстанавливаем соединение…')
        }
      })
      .on(LivekitClient.RoomEvent.Reconnected,()=>{
        if(groupCallState?.room===room){
          groupCallState.reconnectAttempts=0;
          groupCallState.recovering=false;
          groupCallState.needsRecovery=false;
          if(groupCallState.videoNetworkCapTimer){
            clearTimeout(groupCallState.videoNetworkCapTimer);
            groupCallState.videoNetworkCapTimer=null
          }
          groupCallState.pendingVideoNetworkCap='';
          groupCallState.videoNetworkCap='medium';
          setGroupConnectionQuality('good');
          groupCallState.lastParticipantCount=-1;
          refreshGroupCount();
          requestAnimationFrame(()=>scheduleGroupRemoteVideoQualitySync());
          startGroupPingMonitoring(groupCallState)
        }
      })
      .on(LivekitClient.RoomEvent.Disconnected,()=>{
        const state=groupCallState;
        if(!state||state.room!==room||state.manualLeave)return;
        state.needsRecovery=true;
        setGroupConnectionQuality('poor');
        setGroupCallStatus(
          navigator.onLine
            ?'Связь потеряна · переподключаемся…'
            :'Нет интернета · ждём сеть'
        );
        if(navigator.onLine)recoverGroupCall(state).catch(()=>{})
      });

    await room.connect(
      credentials.server_url,
      credentials.participant_token
    );

    if(groupCallState&&!groupCallState.connectedAt){
      groupCallState.connectedAt=Date.now();
      ensureLiveCallDurationTimer()
    }

    ensureGroupTile(room.localParticipant,true);
    updateGroupParticipantQuality(
      room.localParticipant.identity,
      room.localParticipant.connectionQuality
    );
    updateGroupVideoNetworkCap(
      room.localParticipant.connectionQuality,
      room.localParticipant
    );
    for(const participant of room.remoteParticipants.values()){
      ensureGroupTile(participant,false);
      updateGroupMicBadge(participant,false);
      updateGroupParticipantQuality(
        participant.identity,
        participant.connectionQuality
      )
    }

    await room.localParticipant.setMicrophoneEnabled(true);
    groupCallState.muted=false;
    updateGroupMicBadge(room.localParticipant,true,false);
    await startNativeCallService(credentials.group_name,callVideo,true).catch(()=>{});
    if(nativeAudioRoutePlugin()){
      await applyNativeSpeakerMode(callSpeakerMode).catch(()=>{})
    }
    if(callVideo){
      const state=groupCallState;
      if(!state||state.room!==room||state.manualLeave)return false;
      try{
        await retryCameraStart(()=>room.localParticipant.setCameraEnabled(true),()=>groupCallState===state&&!state.manualLeave);
      }catch(err){
        if(groupCallState!==state||state.manualLeave)return false;
        state.cameraOff=true;
        updateGroupCameraControls(state);
        alert(cameraStartErrorMessage(err)+' Разговор продолжится без камеры; её можно включить кнопкой 📷.');
      }
    }

    await renderGroupLocalTracks();
    refreshGroupCount();
    startGroupPingMonitoring(groupCallState);
    startGroupQualityMonitoring(groupCallState);
    return true
  }catch(err){
    try{room?.disconnect()}catch{}
    groupCallState=null;
    hideMiniCallBar();
    stopNativeCallService().catch(()=>{});
    $('groupCallOverlay').classList.add('hidden');
    alert(err?.message||'Не удалось подключиться к групповому звонку');
    return false
  }finally{
    groupCallJoining=false
  }
}

function leaveGroupCall(disconnect=true,endReason='local_leave'){
  if($('groupCallProfileDialog')?.open)$('groupCallProfileDialog').close();
  hideMiniCallBar();
  stopNativeCallService().catch(()=>{});
  resetNativeAudioRoute().catch(()=>{});
  const state=groupCallState;
  if(state){
    cancelOutgoingConferenceInvites(state);
    reportGroupCallQuality(state,endReason,true).catch(()=>{});
    state.manualLeave=true;
    stopGroupPingMonitoring(state);
    stopGroupQualityMonitoring(state);
    if(state.speakerFrame){
      cancelAnimationFrame(state.speakerFrame);
      state.speakerFrame=0
    }
    if(state.speakerFocusTimer){
      clearTimeout(state.speakerFocusTimer);
      state.speakerFocusTimer=null
    }
    if(state.videoQualityFrame){
      cancelAnimationFrame(state.videoQualityFrame);
      state.videoQualityFrame=0
    }
    if(state.videoNetworkCapTimer){
      clearTimeout(state.videoNetworkCapTimer);
      state.videoNetworkCapTimer=null
    }
    state.pendingVideoNetworkCap=''
  }
  groupCallState=null;
  window.SvoiAdminMasks?.scheduleGroup();
  if(state&&disconnect){
    try{state.room.disconnect()}catch{}
  }
  $('groupCallGrid').replaceChildren();
  $('groupCallStrip').replaceChildren();
  $('groupCallStrip').classList.add('hidden');
  $('groupCallGrid').classList.remove(
    'has-focus',
    'layout-1',
    'layout-2',
    'layout-3-4',
    'layout-5-6',
    'layout-7-9',
    'layout-10'
  );
  setGroupConnectionQuality('unknown');
  $('groupCallDuration').textContent='00:00';
  $('groupCallDuration').classList.add('hidden');
  $('groupCallOverlay').classList.remove('group-focused-mode');
  $('groupCallOverlay').classList.add('hidden');
  $('groupInviteLinkWrap').classList.add('hidden');
  stopLiveCallDurationTimerIfIdle()
}

window.addEventListener('resize',()=>{
  if(groupCallState) scheduleGroupRemoteVideoQualitySync();
  if(currentCall&&localVideoTrack()){
    requestAnimationFrame(()=>applyPrivateLocalVideoPosition())
  }
},{passive:true});

window.addEventListener('offline',()=>{
  if(groupCallState){
    groupCallState.needsRecovery=true;
    setGroupCallStatus('Нет интернета · ждём сеть')
  }

  const call=currentCall;
  if(call?.answered){
    if(call.recoveryTimer){
      clearTimeout(call.recoveryTimer);
      call.recoveryTimer=null
    }
    call.recovering=false;
    $('callStatus').textContent='Нет интернета · ждём сеть';
    updateMiniCallBar()
  }
});

window.addEventListener('online',()=>{
  ensureWsConnection();
  const state=groupCallState;
  if(state&&state.needsRecovery&&!state.manualLeave){
    setGroupCallStatus('Интернет появился · переподключаемся…');
    recoverGroupCall(state).catch(()=>{})
  }

  const call=currentCall;
  if(
    call?.answered
    && call.pc
    && !['connected','closed'].includes(call.pc.connectionState)
  ){
    if(!call.recovering)call.recoveryAttempts=0;
    schedulePrivateCallRecovery(call,120)
  }
});

$('closeGroupCallProfile').onclick=()=>$('groupCallProfileDialog').close();
$('groupCallProfileDialog').addEventListener('click',event=>{
  if(event.target===$('groupCallProfileDialog')){
    $('groupCallProfileDialog').close()
  }
});
$('groupCallProfileDialog').addEventListener('close',()=>{
  groupCallProfileUser=null
});

$('groupCallProfileFocus').onclick=()=>{
  const tile=groupCallProfileUser?.tile;
  $('groupCallProfileDialog').close();
  if(tile?.isConnected)focusGroupTile(tile)
};

$('groupCallProfilePin').onclick=()=>{
  const identity=String(groupCallProfileUser?.identity||'');
  if(!groupCallState||!identity)return;
  const isPinned=groupCallState.pinnedIdentity===identity;
  setGroupPinnedIdentity(isPinned?'':identity);
  $('groupCallProfileDialog').close()
};

$('groupCallProfileMessage').onclick=async()=>{
  const user=groupCallProfileUser?.user;
  if(!user?.id||Number(user.id)===Number(me?.id))return;
  $('groupCallProfileDialog').close();
  minimizeGroupCall();
  let chatUser=users.find(item=>Number(item.id)===Number(user.id));
  if(!chatUser){
    chatUser=user;
    if(!users.some(item=>Number(item.id)===Number(user.id))){
      users=[chatUser,...users];
      renderUsers()
    }
  }
  await openUser(chatUser)
};

$('groupCallProfileMute').onclick=()=>{
  const state=groupCallState;
  const user=groupCallProfileUser?.user;
  if(!state?.isAdmin||!user?.id||Number(user.id)===Number(me?.id))return;
  try{
    wsSend({
      type:'group_force_mute',
      group_id:state.groupId,
      target_user_id:Number(user.id)
    });
    $('groupCallProfileMute').disabled=true;
    setTimeout(()=>{
      if($('groupCallProfileMute'))$('groupCallProfileMute').disabled=false
    },1200)
  }catch{}
};



let callInviteContext=null,callInviteSending=false;
const outgoingConferenceInvites=new Map(),conferenceInviteStatuses=new Map();

function callInviteParticipants(){
  const ids=new Set([Number(me?.id)]);
  if(currentCall)ids.add(Number(currentCall.peerId));
  for(const participant of groupCallState?.room?.remoteParticipants?.values?.()||[]){
    const match=/^user-(\d+)$/.exec(String(participant.identity||''));
    if(match)ids.add(Number(match[1]));
  }
  return ids;
}
function renderCallInviteContacts(){
  const list=$('callInviteContacts');
  if(!list)return;
  list.replaceChildren();
  const excluded=callInviteParticipants(),query=$('callInviteSearch').value.trim().toLowerCase();
  const statuses={ringing:'Звоним…',accepted:'Приглашение принято',rejected:'Отклонено',missed:'Не ответил',cancelled:'Отменено'};
  const contacts=users.filter(user=>!excluded.has(Number(user.id))
    &&(!query||(user.display_name+' @'+user.username).toLowerCase().includes(query)));
  for(const user of contacts){
    const item=document.createElement('button');item.type='button';item.className='call-invite-contact';
    const avatar=document.createElement('span');avatar.className='avatar';setAvatar(avatar,user);
    const copy=document.createElement('span');copy.className='call-invite-contact-copy';
    const name=document.createElement('strong');name.textContent=user.display_name;
    const invite=outgoingConferenceInvites.get(Number(user.id));
    const active=invite?.state===groupCallState;
    const detail=document.createElement('small');
    detail.textContent=active?(statuses[invite.status]||''):(user.online?'В сети':'@'+user.username);
    copy.append(name,detail);
    const action=document.createElement('span');action.className='call-invite-contact-action';
    action.textContent=active&&invite.status==='ringing'?'…':'📞';
    item.disabled=callInviteSending||(active&&['ringing','accepted'].includes(invite.status));
    item.append(avatar,copy,action);
    item.onclick=()=>inviteContactToCall(user);
    list.append(item);
  }
  if(!contacts.length){
    const empty=document.createElement('div');empty.className='call-invite-empty';
    empty.textContent=query?'Никого не найдено':'Нет других контактов для приглашения';
    list.append(empty);
  }
}
async function openCallContactPicker(){
  if(!groupCallState&&!currentCall?.answered)return;
  callInviteContext=groupCallState?{kind:'group',state:groupCallState}:{kind:'private',call:currentCall};
  $('callInviteSearch').value='';$('callInviteStatus').textContent='';
  renderCallInviteContacts();
  if(!$('callInviteDialog').open)$('callInviteDialog').showModal();
  try{await loadUsers();if($('callInviteDialog').open)renderCallInviteContacts();}
  catch(err){$('callInviteStatus').textContent=err.message||'Не удалось загрузить контакты';}
}
async function inviteContactToCall(user){
  if(callInviteSending||!callInviteContext)return;
  callInviteSending=true;renderCallInviteContacts();
  try{
    const context=callInviteContext;
    if(context.kind==='private'){
      if(currentCall!==context.call||!context.call.answered)throw new Error('Личный звонок уже завершён');
      $('callInviteStatus').textContent='Подключаем конференцию…';
      const result=await api('/api/calls/'+encodeURIComponent(context.call.callId)+'/invite-link',{method:'POST'});
      if(currentCall!==context.call)throw new Error('Личный звонок уже завершён');
      const promoted=await promotePrivateCallToConference(result.invite_token,!!result.video);
      await api('/api/calls/'+encodeURIComponent(promoted.callId)+'/activate-conference',
        {method:'POST',body:{invite_token:result.invite_token}});
      callInviteContext={kind:'group',state:groupCallState};
    }
    const state=callInviteContext.state;
    if(!state||groupCallState!==state||state.manualLeave)throw new Error('Звонок уже завершён');
    const body={target_user_id:Number(user.id),video:!!(state.video||!state.cameraOff)};
    if(state.kind==='link')body.invite_token=state.inviteToken;
    else body.group_id=Number(state.groupId);
    $('callInviteStatus').textContent='Звоним: '+user.display_name+'…';
    const result=await api('/api/conference-invitations',{method:'POST',body});
    if(groupCallState!==state){
      api('/api/conference-invitations/'+encodeURIComponent(result.call_id)+'/cancel',{method:'POST'}).catch(()=>{});
      throw new Error('Звонок уже завершён');
    }
    outgoingConferenceInvites.set(Number(user.id),{
      callId:result.call_id,state,status:conferenceInviteStatuses.get(result.call_id)||'ringing'
    });
    $('callInviteStatus').textContent='Вызов отправлен: '+user.display_name;
  }catch(err){
    $('callInviteStatus').textContent=err.message||'Не удалось пригласить собеседника';
  }finally{
    callInviteSending=false;renderCallInviteContacts();
  }
}
function handleConferenceInviteStatus(data){
  conferenceInviteStatuses.set(data.call_id,data.status);
  if(conferenceInviteStatuses.size>100)conferenceInviteStatuses.delete(conferenceInviteStatuses.keys().next().value);
  for(const invite of outgoingConferenceInvites.values()){
    if(invite.callId===data.call_id)invite.status=data.status;
  }
  if(pendingCall?.call_id===data.call_id&&data.status==='accepted'&&!acceptingCall){
    finishCall(false);
  }
  if($('callInviteDialog').open)renderCallInviteContacts();
}
function cancelOutgoingConferenceInvites(state){
  for(const [userId,invite] of outgoingConferenceInvites){
    if(invite.state!==state)continue;
    if(invite.status==='ringing'){
      api('/api/conference-invitations/'+encodeURIComponent(invite.callId)+'/cancel',{method:'POST'}).catch(()=>{});
    }
    outgoingConferenceInvites.delete(userId);
  }
  if(callInviteContext?.state===state){
    callInviteContext=null;
    if($('callInviteDialog').open)$('callInviteDialog').close();
  }
}
async function acceptIncomingConferenceInvitation(options={}){
  const data=pendingCall;
  if(!data?.conference_invitation||acceptingCall)return false;
  acceptingCall=true;stopRingtone();clearNativeCallNotification(data.call_id);
  $('acceptCall').disabled=true;$('rejectCall').disabled=true;
  try{
    const result=await api('/api/conference-invitations/'+encodeURIComponent(data.call_id)+'/accept',{method:'POST'});
    if(pendingCall?.call_id!==data.call_id)throw new Error('Приглашение уже завершено');
    pendingCall=null;resetCallUi();
    const joined=await joinGroupCall(null,!!result.video,false,result.invite_token,!!result.video);
    if(!joined)throw new Error('Не удалось подключиться к конференции');
    return true;
  }catch(err){
    pendingCall=null;resetCallUi();
    if(!options.suppressFailureAlert)alert(err.message||'Не удалось принять приглашение');
    return false;
  }finally{
    acceptingCall=false;$('acceptCall').disabled=false;$('rejectCall').disabled=false;
  }
}

$('privateInviteCall').onclick=openCallContactPicker;
$('groupInviteLinkBtn').onclick=openCallContactPicker;
$('closeCallInvite').onclick=()=>$('callInviteDialog').close();
$('callInviteSearch').oninput=renderCallInviteContacts;
$('callInviteDialog').addEventListener('click',event=>{
  if(event.target===$('callInviteDialog'))$('callInviteDialog').close();
});

$('groupMuteBtn').onclick=async()=>{
  if(!groupCallState)return;
  try{
    groupCallState.muted=!groupCallState.muted;
    await groupCallState.room.localParticipant.setMicrophoneEnabled(!groupCallState.muted);
    updateGroupMicBadge(
      groupCallState.room.localParticipant,
      true,
      groupCallState.muted
    );
    $('groupMuteBtn').classList.toggle('off',groupCallState.muted);
    $('groupMuteBtn').textContent=groupCallState.muted?'🔇':'🎤'
  }catch(err){alert(err.message||'Не удалось изменить микрофон')}
};

$('groupSpeakerBtn').onclick=()=>chooseSpeakerOutput();


$('groupCameraBtn').onclick=async()=>{
  const state=groupCallState;
  if(!state||state.cameraChanging)return;
  const nextOff=!state.cameraOff;
  state.cameraChanging=true;
  updateGroupCameraControls(state);
  try{
    if(nextOff)await state.room.localParticipant.setCameraEnabled(false);
    else await retryCameraStart(
      ()=>state.room.localParticipant.setCameraEnabled(true),
      ()=>groupCallState===state&&!state.manualLeave
    );
    if(groupCallState!==state)return;
    state.cameraOff=nextOff;
    if(!nextOff)startNativeCallService($('groupCallName').textContent,true,true).catch(()=>{});
    await renderGroupLocalTracks();
    updateGroupScreenShareControls();
  }catch(err){
    if(groupCallState===state)alert(cameraStartErrorMessage(err,'Не удалось изменить камеру'));
  }finally{
    state.cameraChanging=false;
    updateGroupCameraControls(state);
  }
};

$('groupSwitchCameraBtn').onclick=async()=>{
  const state=groupCallState;
  if(!state||state.cameraOff||state.cameraChanging)return;
  const isActive=()=>groupCallState===state&&!state.manualLeave;
  let videoTrack=null;
  for(const pub of state.room.localParticipant.videoTrackPublications.values()){
    if(pub.track){videoTrack=pub.track;break;}
  }
  if(!videoTrack){alert('Камера не найдена');return;}
  const oldFacing=state.facing;
  const nextFacing=oldFacing==='user'?'environment':'user';
  state.cameraChanging=true;
  updateGroupCameraControls(state);
  try{
    await retryCameraStart(()=>videoTrack.restartTrack({facingMode:nextFacing}),isActive);
    if(!isActive())return;
    state.facing=nextFacing;
    await renderGroupLocalTracks();
  }catch(err){
    if(!isActive())return;
    try{
      await retryCameraStart(()=>videoTrack.restartTrack({facingMode:oldFacing}),isActive);
    }catch{
      state.cameraOff=true;
      try{await state.room.localParticipant.setCameraEnabled(false)}catch{}
    }
    if(isActive()){
      await renderGroupLocalTracks();
      alert(cameraStartErrorMessage(err,'Не удалось переключить камеру'));
    }
  }finally{
    state.cameraChanging=false;
    updateGroupCameraControls(state);
  }
};

$('groupEndBtn').onclick=()=>leaveGroupCall(true);
$('groupCallBack').onclick=()=>minimizeGroupCall();

async function resumeGroupCallFromUrl(){
  const params=new URLSearchParams(location.search);
  const groupId=Number(params.get('group_call'));
  if(!groupId)return;
  const video=params.get('video')==='1';
  history.replaceState({},'',location.pathname);
  await joinGroupCall(groupId,video,false)
}

async function resumeCallInviteFromUrl(){
  const params=new URLSearchParams(location.search);
  const inviteToken=String(params.get('call_invite')||'').trim();
  if(!inviteToken)return;

  history.replaceState({},'',location.pathname);
  if(currentCall||pendingCall||groupCallState){
    alert('Сначала заверши текущий звонок');
    return
  }

  const join=confirm('Присоединиться к звонку по приглашению?');
  if(!join)return;

  try{
    const joined=await joinGroupCall(null,false,false,inviteToken);
    if(!joined)return
  }catch(err){
    alert(err?.message||'Не удалось присоединиться по ссылке')
  }
}

let rtcConfigCache=null,rtcConfigFetchedAt=0;
let prewarmedPrivatePc=null;
let prewarmedPrivatePeerId=0;
let prewarmedPrivateTimer=null;
let prewarmPrivatePromise=null;

function closePrewarmedPrivateCall(){
  if(prewarmedPrivateTimer)clearTimeout(prewarmedPrivateTimer);
  prewarmedPrivateTimer=null;
  const pc=prewarmedPrivatePc;
  prewarmedPrivatePc=null;
  prewarmedPrivatePeerId=0;
  try{pc?.close()}catch{}
}

async function prewarmPrivateCall(peerId){
  peerId=Number(peerId)||0;
  if(!peerId||currentCall||pendingCall||groupCallState)return null;
  if(
    prewarmedPrivatePc
    &&prewarmedPrivatePeerId===peerId
    &&prewarmedPrivatePc.connectionState!=='closed'
  )return prewarmedPrivatePc;
  if(prewarmPrivatePromise)return prewarmPrivatePromise;

  prewarmPrivatePromise=(async()=>{
    const config=await getRtcConfig();
    if(currentCall||pendingCall||groupCallState)return null;
    if(active?.type==='user'&&Number(active.data.id)!==peerId)return null;
    closePrewarmedPrivateCall();
    const pc=new RTCPeerConnection(config);
    prewarmedPrivatePc=pc;
    prewarmedPrivatePeerId=peerId;
    prewarmedPrivateTimer=setTimeout(()=>{
      if(prewarmedPrivatePc===pc)closePrewarmedPrivateCall()
    },20000);
    return pc
  })().finally(()=>{prewarmPrivatePromise=null});
  return prewarmPrivatePromise
}

function takePrewarmedPrivateCall(peerId){
  peerId=Number(peerId)||0;
  const pc=prewarmedPrivatePc;
  if(
    !pc
    ||prewarmedPrivatePeerId!==peerId
    ||pc.connectionState==='closed'
  ){
    if(pc)closePrewarmedPrivateCall();
    return null
  }
  if(prewarmedPrivateTimer)clearTimeout(prewarmedPrivateTimer);
  prewarmedPrivateTimer=null;
  prewarmedPrivatePc=null;
  prewarmedPrivatePeerId=0;
  return pc
}

async function getRtcConfig(){
  const now=Date.now();
  if(rtcConfigCache&&now-rtcConfigFetchedAt<30*60*1000)return rtcConfigCache;
  try{
    const data=await api('/api/turn');
    rtcConfigCache={
      iceServers:data.ice_servers||[
        {urls:['stun:stun.l.google.com:19302']}
      ],
      iceCandidatePoolSize:4,
      bundlePolicy:'max-bundle',
      rtcpMuxPolicy:'require'
    };
  }catch{
    rtcConfigCache={
      iceServers:[
        {urls:['stun:stun.l.google.com:19302']},
        {urls:['stun:stun1.l.google.com:19302']}
      ],
      iceCandidatePoolSize:4,
      bundlePolicy:'max-bundle',
      rtcpMuxPolicy:'require'
    };
  }
  rtcConfigFetchedAt=now;
  return rtcConfigCache
}

function wsSend(payload){
  if(!socket||socket.readyState!==WebSocket.OPEN)throw new Error('Нет соединения с сервером');
  socket.send(JSON.stringify(payload))
}

function callId(){return crypto.randomUUID?crypto.randomUUID():(Date.now()+'-'+Math.random().toString(16).slice(2))}

function privateVideoAvailable(){
  return !!currentCall&&(
    !!localVideoTrack()?.enabled
    || (!!currentCall.remoteVideo&&!currentCall.remoteCameraOff)
  )
}

function applyPrivateLocalVideoPosition(){
  const stage=document.querySelector('#callOverlay .call-stage');
  const local=$('localVideo');
  if(!stage||!local)return;

  const stageRect=stage.getBoundingClientRect();
  const localRect=local.getBoundingClientRect();
  if(!stageRect.width||!stageRect.height||!localRect.width||!localRect.height)return;

  const padding=10;
  const maxLeft=Math.max(
    padding,
    stageRect.width-localRect.width-padding
  );
  const maxTop=Math.max(
    padding,
    stageRect.height-localRect.height-padding
  );
  const availableX=Math.max(0,maxLeft-padding);
  const availableY=Math.max(0,maxTop-padding);
  const left=padding+availableX*privateLocalVideoPosition.x;
  const top=padding+availableY*privateLocalVideoPosition.y;

  local.style.left=Math.round(left)+'px';
  local.style.top=Math.round(top)+'px'
}

function savePrivateLocalVideoPosition(left,top){
  const stage=document.querySelector('#callOverlay .call-stage');
  const local=$('localVideo');
  if(!stage||!local)return;

  const stageRect=stage.getBoundingClientRect();
  const localRect=local.getBoundingClientRect();
  const padding=10;
  const maxLeft=Math.max(padding,stageRect.width-localRect.width-padding);
  const maxTop=Math.max(padding,stageRect.height-localRect.height-padding);
  const availableX=Math.max(1,maxLeft-padding);
  const availableY=Math.max(1,maxTop-padding);

  privateLocalVideoPosition={
    x:Math.max(0,Math.min(1,(left-padding)/availableX)),
    y:Math.max(0,Math.min(1,(top-padding)/availableY))
  };
  localStorage.setItem(
    'svoi_private_pip_position',
    JSON.stringify(privateLocalVideoPosition)
  )
}

function bindPrivateLocalVideoDrag(){
  const local=$('localVideo');
  const stage=document.querySelector('#callOverlay .call-stage');
  if(!local||!stage||local.dataset.dragBound==='1')return;
  local.dataset.dragBound='1';

  let drag=null;

  local.addEventListener('pointerdown',event=>{
    if(event.button!=null&&event.button!==0)return;
    if(local.classList.contains('hidden'))return;

    const localRect=local.getBoundingClientRect();
    const stageRect=stage.getBoundingClientRect();
    drag={
      pointerId:event.pointerId,
      offsetX:event.clientX-localRect.left,
      offsetY:event.clientY-localRect.top,
      stageRect
    };

    local.classList.add('dragging');
    try{local.setPointerCapture(event.pointerId)}catch{}
    event.preventDefault()
  });

  local.addEventListener('pointermove',event=>{
    if(!drag||event.pointerId!==drag.pointerId)return;
    const localRect=local.getBoundingClientRect();
    const padding=10;
    const maxLeft=Math.max(
      padding,
      drag.stageRect.width-localRect.width-padding
    );
    const maxTop=Math.max(
      padding,
      drag.stageRect.height-localRect.height-padding
    );
    const left=Math.max(
      padding,
      Math.min(maxLeft,event.clientX-drag.stageRect.left-drag.offsetX)
    );
    const top=Math.max(
      padding,
      Math.min(maxTop,event.clientY-drag.stageRect.top-drag.offsetY)
    );

    local.style.left=Math.round(left)+'px';
    local.style.top=Math.round(top)+'px';
    event.preventDefault()
  },{passive:false});

  const finish=event=>{
    if(!drag||event.pointerId!==drag.pointerId)return;
    const left=parseFloat(local.style.left)||10;
    const top=parseFloat(local.style.top)||10;
    savePrivateLocalVideoPosition(left,top);
    drag=null;
    local.classList.remove('dragging')
  };

  local.addEventListener('pointerup',finish);
  local.addEventListener('pointercancel',event=>{
    if(!drag||event.pointerId!==drag.pointerId)return;
    drag=null;
    local.classList.remove('dragging');
    applyPrivateLocalVideoPosition()
  })
}

function applyPrivateParticipantSize(){
  const remote=$('remoteVideo');
  const local=$('localVideo');
  const down=$('privateSizeDown');
  const up=$('privateSizeUp');
  const available=privateVideoAvailable();

  down?.classList.toggle('hidden',!available);
  up?.classList.toggle('hidden',!available);
  if(down)down.disabled=!available||privateParticipantSize<=0;
  if(up)up.disabled=!available||privateParticipantSize>=2;

  const remoteScales=[0.88,1,1.14];
  const localWidths=['21%','30%','41%'];
  const localMax=['120px','180px','240px'];

  if(remote){
    remote.style.transform='scale('+remoteScales[privateParticipantSize]+')';
    remote.style.borderRadius=privateParticipantSize===0?'18px':'0'
  }
  if(local){
    local.style.width=localWidths[privateParticipantSize];
    local.style.maxWidth=localMax[privateParticipantSize];
    requestAnimationFrame(()=>applyPrivateLocalVideoPosition())
  }
}

function changePrivateParticipantSize(delta){
  const next=Math.max(0,Math.min(2,privateParticipantSize+delta));
  if(next===privateParticipantSize)return;
  privateParticipantSize=next;
  localStorage.setItem('svoi_private_video_size',String(next));
  applyPrivateParticipantSize()
}

function nativeProximityPlugin(){
  return window.Capacitor?.Plugins?.NativeProximity||null
}

let nativeProximityEnabled=false;

function canUseEarMode(){
  return !!currentCall
    && !!currentCall.answered
    && !localVideoTrack()
    && !currentCall.remoteVideo
}

function shouldUseNativeProximity(){
  return !!nativeProximityPlugin()
    && canUseEarMode()
    && !callSpeakerMode
}

async function syncNativeProximity(){
  const plugin=nativeProximityPlugin();
  if(!plugin){
    nativeProximityEnabled=false;
    return
  }

  const shouldEnable=shouldUseNativeProximity();
  if(shouldEnable===nativeProximityEnabled)return;

  try{
    if(shouldEnable){
      await plugin.enable();
      nativeProximityEnabled=true
    }else{
      await plugin.disable();
      nativeProximityEnabled=false
    }
  }catch{
    nativeProximityEnabled=false
  }
}

function updateEarModeButton(){
  const button=$('earModeBtn');
  if(!button)return;

  const nativeAutomatic=!!nativeProximityPlugin();
  const available=canUseEarMode();

  // В нативном Android датчик приближения работает автоматически.
  // В браузере/PWA оставляем ручную кнопку режима у уха.
  button.classList.toggle('hidden',nativeAutomatic||!available);
  button.disabled=nativeAutomatic||!available;
  button.title=nativeAutomatic
    ?'Датчик приближения работает автоматически'
    :'Режим у уха';

  syncNativeProximity().catch(()=>{})
}

function enterEarMode(){
  if(!canUseEarMode())return;
  earModeActive=true;
  const lock=$('earLock');
  $('earLockTitle').textContent='Режим у уха';
  $('earLockText').textContent='Экран заблокирован. Чтобы разблокировать — удерживайте экран 2 секунды.';
  lock.classList.remove('holding');
  lock.classList.remove('hidden')
}

function cancelEarUnlock(){
  if(earUnlockTimer){
    clearTimeout(earUnlockTimer);
    earUnlockTimer=null
  }
  const lock=$('earLock');
  lock?.classList.remove('holding');
  if(earModeActive){
    $('earLockTitle').textContent='Режим у уха';
    $('earLockText').textContent='Экран заблокирован. Чтобы разблокировать — удерживайте экран 2 секунды.'
  }
}

function exitEarMode(){
  cancelEarUnlock();
  earModeActive=false;
  $('earLock')?.classList.add('hidden')
}

function beginEarUnlock(event){
  if(!earModeActive)return;
  event?.preventDefault?.();
  cancelEarUnlock();
  $('earLock').classList.add('holding');
  $('earLockTitle').textContent='Удерживайте…';
  $('earLockText').textContent='Не отпускайте экран 2 секунды';
  earUnlockTimer=setTimeout(()=>{
    earUnlockTimer=null;
    exitEarMode()
  },2000)
}

function localVideoTrack(){
  return currentCall?.localStream?.getVideoTracks()?.find(track=>track.readyState==='live')||null
}

function restorePrivateLocalPreview(call=currentCall){
  if(!call||currentCall!==call)return;
  const preview=$('localVideo');
  const camera=call.localStream?.getVideoTracks?.().find(track=>track.readyState==='live')||null;
  if(camera){
    preview.srcObject=call.localStream;
    preview.classList.toggle('hidden',!camera.enabled);
    if(camera.enabled)preview.play().catch(()=>{})
  }else{
    preview.srcObject=null;
    preview.classList.add('hidden')
  }
}

function updatePrivateVideoControls(){
  const cameraButton=$('cameraCall');
  const switchButton=$('switchCamera');
  const preview=$('localVideo');
  if(!cameraButton||!switchButton||!preview)return;

  const track=localVideoTrack();
  const hasVideo=!!track;
  const enabled=!!track?.enabled;

  cameraButton.classList.toggle('off',hasVideo&&!enabled);
  cameraButton.textContent=!hasVideo?'🎥':(enabled?'📷':'🚫');
  cameraButton.title=!hasVideo
    ?'Переключить в видеорежим'
    :(enabled?'Переключить в аудиорежим':'Переключить в видеорежим');

  cameraButton.disabled=false;
  switchButton.classList.toggle('hidden',!hasVideo);
  preview.classList.toggle('hidden',!hasVideo||!enabled);
  updateEarModeButton();
  applyPrivateParticipantSize();
  if(hasVideo&&enabled){
    requestAnimationFrame(()=>applyPrivateLocalVideoPosition())
  }
  window.SvoiAdminMasks?.schedulePrivate();
}

async function upgradeCurrentCallToVideo(){
  if(!currentCall||currentCall.renegotiating)return;
  const upgradingCall=currentCall;
  exitEarMode();
  if(!currentCall.answered){
    alert('Дождись, пока собеседник примет звонок');
    return
  }
  currentCall.renegotiating=true;
  const button=$('cameraCall');
  if(button)button.disabled=true;

  let newTrack=null;
  let sender=null;
  try{
    const videoStream=await retryCameraStart(()=>navigator.mediaDevices.getUserMedia({
      video:getCallVideoConstraints(),
      audio:false
    }),()=>currentCall===upgradingCall&&upgradingCall.pc?.signalingState!=='closed');
    newTrack=videoStream.getVideoTracks()[0];
    if(!newTrack)throw new Error('Камера не открылась');

    currentCall.localStream.addTrack(newTrack);
    sender=currentCall.pc.addTrack(newTrack,currentCall.localStream);
    currentCall.video=true;
    currentCall.cameraOff=false;
    await applyPrivateVideoTier(currentCall,currentCall.appliedVideoTier||'good').catch(()=>{});
    startNativeCallService(currentCall.peerName,true,false).catch(()=>{});

    newTrack.onended=()=>{
      try{currentCall?.localStream?.removeTrack(newTrack)}catch{}
      updatePrivateVideoControls()
    };

    $('localVideo').srcObject=currentCall.localStream;
    updatePrivateLocalMirror();
    $('localVideo').play().catch(()=>{});
    updatePrivateVideoControls();

    $('callStatus').textContent='Подключаем видео…';
    const offer=await currentCall.pc.createOffer();
    await currentCall.pc.setLocalDescription(offer);
    await waitForSocketOpen();
    wsSend({
      type:'call_video_offer',
      to_user_id:currentCall.peerId,
      call_id:currentCall.callId,
      video:true,
      sdp:currentCall.pc.localDescription.toJSON
        ?currentCall.pc.localDescription.toJSON()
        :currentCall.pc.localDescription
    })
  }catch(err){
    if(sender&&currentCall?.pc){
      try{currentCall.pc.removeTrack(sender)}catch{}
    }
    if(newTrack){
      try{currentCall?.localStream?.removeTrack(newTrack)}catch{}
      try{newTrack.stop()}catch{}
    }
    updatePrivateVideoControls();
    $('callStatus').textContent='Соединено';
    alert(err?.name==='NotAllowedError'
      ?'Разреши доступ к камере для этого сайта'
      :cameraStartErrorMessage(err,'Не удалось включить видео'))
  }finally{
    if(currentCall)currentCall.renegotiating=false;
    if(button)button.disabled=false
  }
}

let callPingTimer=null;
const peerNetworkBaselines=new WeakMap();
const peerMediaBaselines=new WeakMap();

function setCallPing(value,network=null){
  const badge=$('callPing');
  if(!badge)return;
  const ms=Number(value);
  badge.classList.remove('good','medium','poor','checking');
  if(!Number.isFinite(ms)||ms<0){
    badge.classList.add('hidden');
    badge.textContent='— мс';
    badge.title='';
    return
  }
  const rounded=Math.max(1,Math.round(ms));
  const loss=Number(network?.packetLossPct);
  const jitter=Number(network?.jitterMs);
  const level=(
    (Number.isFinite(loss)&&loss>=10)
    ||(Number.isFinite(jitter)&&jitter>=100)
    ||rounded>350
  )?'poor':(
    (Number.isFinite(loss)&&loss>=4)
    ||(Number.isFinite(jitter)&&jitter>=50)
    ||rounded>150
  )?'medium':'good';
  const icon=level==='good'?'🟢':(level==='medium'?'🟡':'🔴');
  badge.classList.add(level);
  badge.textContent=icon+' '+rounded+' мс';
  const details=[
    level==='good'?'Связь хорошая':(level==='medium'?'Связь средняя':'Связь плохая')
  ];
  if(Number.isFinite(loss))details.push('Потери '+loss.toFixed(loss>=10?0:1)+'%');
  if(Number.isFinite(jitter))details.push('Jitter '+Math.round(jitter)+' мс');
  badge.title=details.join(' · ');
  badge.classList.remove('hidden')
}

function stopCallPing(){
  if(callPingTimer){
    clearInterval(callPingTimer);
    callPingTimer=null
  }
  setCallPing(null)
}

function networkLossSample(previous,currentSent,currentLost,receivedMode=false){
  if(!previous)return null;
  const sentDelta=currentSent-previous.sent;
  const lostDelta=currentLost-previous.lost;
  if(sentDelta<0||lostDelta<0)return null;
  const denominator=receivedMode?sentDelta+lostDelta:sentDelta;
  if(denominator<20)return null;
  return Math.max(0,Math.min(100,(lostDelta/Math.max(1,denominator))*100))
}

async function readPeerNetworkStats(pc){
  if(!pc?.getStats){
    return {
      rttMs:null,
      availableOutgoingBitrate:null,
      packetLossPct:null,
      jitterMs:null
    }
  }
  const stats=await pc.getStats();
  let pair=null;
  let transportPairId=null;

  stats.forEach(report=>{
    if(report.type==='transport'&&report.selectedCandidatePairId){
      transportPairId=report.selectedCandidatePairId
    }
  });

  if(transportPairId&&typeof stats.get==='function'){
    pair=stats.get(transportPairId)||null
  }

  if(!pair){
    stats.forEach(report=>{
      if(
        !pair
        && report.type==='candidate-pair'
        && report.state==='succeeded'
        && (report.nominated||report.selected)
      ){
        pair=report
      }
    })
  }

  let rttMs=null;
  const pairRtt=Number(pair?.currentRoundTripTime);
  if(Number.isFinite(pairRtt)&&pairRtt>=0){
    rttMs=pairRtt*1000
  }else{
    stats.forEach(report=>{
      if(
        rttMs==null
        && report.type==='remote-inbound-rtp'
        && Number.isFinite(Number(report.roundTripTime))
      ){
        rttMs=Number(report.roundTripTime)*1000
      }
    })
  }

  const previous=peerNetworkBaselines.get(pc)||new Map();
  const next=new Map();
  const previousMedia=peerMediaBaselines.get(pc)||new Map();
  const nextMedia=new Map();
  let videoBitrateBps=null;
  let videoWidth=null;
  let videoHeight=null;
  let videoFps=null;
  const remoteLoss=[];
  const inboundLoss=[];
  const remoteJitter=[];
  const inboundJitter=[];

  stats.forEach(report=>{
    const kind=report.kind||report.mediaType;
    if(kind!=='audio'&&kind!=='video')return;

    if(report.type==='outbound-rtp'&&kind==='video'){
      const bytes=Number(report.bytesSent);
      const timestamp=Number(report.timestamp);
      const key='out-video:'+report.id;
      const prev=previousMedia.get(key);
      if(
        prev
        &&Number.isFinite(bytes)
        &&Number.isFinite(timestamp)
        &&bytes>=prev.bytes
        &&timestamp>prev.timestamp
      ){
        videoBitrateBps=((bytes-prev.bytes)*8)/((timestamp-prev.timestamp)/1000)
      }
      if(Number.isFinite(bytes)&&Number.isFinite(timestamp)){
        nextMedia.set(key,{bytes,timestamp})
      }
      const width=Number(report.frameWidth);
      const height=Number(report.frameHeight);
      const fps=Number(report.framesPerSecond);
      if(Number.isFinite(width)&&width>0)videoWidth=width;
      if(Number.isFinite(height)&&height>0)videoHeight=height;
      if(Number.isFinite(fps)&&fps>=0)videoFps=fps
    }

    if(report.type==='remote-inbound-rtp'){
      const jitter=Number(report.jitter);
      if(Number.isFinite(jitter)&&jitter>=0){
        remoteJitter.push(jitter*1000)
      }

      const lost=Number(report.packetsLost);
      const local=report.localId&&typeof stats.get==='function'
        ?stats.get(report.localId)
        :null;
      const sent=Number(local?.packetsSent);
      if(Number.isFinite(lost)&&Number.isFinite(sent)){
        const key='remote:'+report.id;
        const sample=networkLossSample(previous.get(key),sent,lost,false);
        next.set(key,{sent,lost});
        if(sample!=null)remoteLoss.push(sample)
      }

      const fraction=Number(report.fractionLost);
      if(
        !remoteLoss.length
        && Number.isFinite(fraction)
        && fraction>=0
        && fraction<=1
      ){
        remoteLoss.push(fraction*100)
      }
      return
    }

    if(report.type==='inbound-rtp'){
      const jitter=Number(report.jitter);
      if(Number.isFinite(jitter)&&jitter>=0){
        inboundJitter.push(jitter*1000)
      }

      const lost=Number(report.packetsLost);
      const received=Number(report.packetsReceived);
      if(Number.isFinite(lost)&&Number.isFinite(received)){
        const key='inbound:'+report.id;
        const sample=networkLossSample(previous.get(key),received,lost,true);
        next.set(key,{sent:received,lost});
        if(sample!=null)inboundLoss.push(sample)
      }
    }
  });

  peerNetworkBaselines.set(pc,next);
  peerMediaBaselines.set(pc,nextMedia);

  const localVideo=currentCall?.pc===pc?localVideoTrack():null;
  if(localVideo){
    try{
      const settings=localVideo.getSettings?.()||{};
      if(videoWidth==null&&Number(settings.width)>0)videoWidth=Number(settings.width);
      if(videoHeight==null&&Number(settings.height)>0)videoHeight=Number(settings.height);
      if(videoFps==null&&Number(settings.frameRate)>=0)videoFps=Number(settings.frameRate)
    }catch{}
  }

  const lossSamples=remoteLoss.length?remoteLoss:inboundLoss;
  const jitterSamples=remoteJitter.length?remoteJitter:inboundJitter;
  const outgoing=Number(pair?.availableOutgoingBitrate);

  return {
    rttMs,
    availableOutgoingBitrate:Number.isFinite(outgoing)&&outgoing>0?outgoing:null,
    packetLossPct:lossSamples.length?Math.max(...lossSamples):null,
    jitterMs:jitterSamples.length?Math.max(...jitterSamples):null,
    videoBitrateBps:Number.isFinite(videoBitrateBps)&&videoBitrateBps>=0?videoBitrateBps:null,
    videoWidth:Number.isFinite(videoWidth)&&videoWidth>0?Math.round(videoWidth):null,
    videoHeight:Number.isFinite(videoHeight)&&videoHeight>0?Math.round(videoHeight):null,
    videoFps:Number.isFinite(videoFps)&&videoFps>=0?videoFps:null
  }
}

function privateVideoTierForNetwork(network){
  const rtt=Number(network?.rttMs);
  const bandwidth=Number(network?.availableOutgoingBitrate);
  const loss=Number(network?.packetLossPct);
  const jitter=Number(network?.jitterMs);
  const hasRtt=Number.isFinite(rtt)&&rtt>=0;
  const hasBandwidth=Number.isFinite(bandwidth)&&bandwidth>0;
  const hasLoss=Number.isFinite(loss)&&loss>=0;
  const hasJitter=Number.isFinite(jitter)&&jitter>=0;

  // Video yields before speech. These thresholds are intentionally lower
  // than the audio thresholds so a congested link first sheds pixels/FPS.
  if(
    (hasRtt&&rtt>=720)
    ||(hasBandwidth&&bandwidth<520000)
    ||(hasLoss&&loss>=9)
    ||(hasJitter&&jitter>=90)
  )return 'poor';

  if(
    (hasRtt&&rtt>=340)
    ||(hasBandwidth&&bandwidth<1250000)
    ||(hasLoss&&loss>=3.5)
    ||(hasJitter&&jitter>=42)
  )return 'fair';

  return 'good'
}

function privateAudioTierForNetwork(network){
  const rtt=Number(network?.rttMs);
  const bandwidth=Number(network?.availableOutgoingBitrate);
  const loss=Number(network?.packetLossPct);
  const jitter=Number(network?.jitterMs);
  const hasRtt=Number.isFinite(rtt)&&rtt>=0;
  const hasBandwidth=Number.isFinite(bandwidth)&&bandwidth>0;
  const hasLoss=Number.isFinite(loss)&&loss>=0;
  const hasJitter=Number.isFinite(jitter)&&jitter>=0;

  // Keep speech quality high until the connection is genuinely constrained.
  if(
    (hasRtt&&rtt>=1100)
    ||(hasBandwidth&&bandwidth<120000)
    ||(hasLoss&&loss>=18)
    ||(hasJitter&&jitter>=160)
  )return 'poor';

  if(
    (hasRtt&&rtt>=700)
    ||(hasBandwidth&&bandwidth<240000)
    ||(hasLoss&&loss>=9)
    ||(hasJitter&&jitter>=90)
  )return 'fair';

  return 'good'
}

function severePrivateNetworkProblem(network){
  const rtt=Number(network?.rttMs);
  const bandwidth=Number(network?.availableOutgoingBitrate);
  const loss=Number(network?.packetLossPct);
  const jitter=Number(network?.jitterMs);
  return (
    (Number.isFinite(rtt)&&rtt>=850)
    ||(Number.isFinite(bandwidth)&&bandwidth>0&&bandwidth<300000)
    ||(Number.isFinite(loss)&&loss>=14)
    ||(Number.isFinite(jitter)&&jitter>=125)
  )
}

function networkTierRank(tier){
  return tier==='good'?2:(tier==='fair'?1:0)
}

async function applyPrivateVideoTier(call,tier,network=null){
  if(!call||currentCall!==call||!call.pc)return;
  const sender=call.pc.getSenders().find(item=>item.track?.kind==='video');
  if(!sender?.track||typeof sender.getParameters!=='function'||typeof sender.setParameters!=='function')return;

  const profiles={
    // Source is 1280x720. scaleResolutionDownBy affects only the encoded
    // stream, so the local preview can remain sharp.
    good:{
      maxBitrate:2800000,
      maxFramerate:30,
      scaleResolutionDownBy:1
    },
    fair:{
      maxBitrate:1350000,
      maxFramerate:24,
      scaleResolutionDownBy:1.35
    },
    poor:{
      maxBitrate:560000,
      maxFramerate:15,
      scaleResolutionDownBy:2
    }
  };
  const profile=profiles[tier]||profiles.good;
  let maxBitrate=profile.maxBitrate;

  const bandwidth=Number(network?.availableOutgoingBitrate);
  if(Number.isFinite(bandwidth)&&bandwidth>0){
    // Always leave room for Opus, RTCP and retransmissions. On a very weak
    // link the video budget is allowed to fall far enough to protect speech.
    const reservedForAudioAndOverhead=105000;
    const videoBudget=Math.max(
      90000,
      bandwidth-reservedForAudioAndOverhead
    );
    maxBitrate=Math.min(maxBitrate,videoBudget)
  }

  try{
    const previousTier=call.appliedVideoTier||null;
    const params=sender.getParameters();
    if(!Array.isArray(params.encodings)||!params.encodings.length){
      params.encodings=[{}]
    }
    for(const encoding of params.encodings){
      encoding.maxBitrate=Math.round(maxBitrate);
      encoding.maxFramerate=profile.maxFramerate;
      encoding.scaleResolutionDownBy=profile.scaleResolutionDownBy
    }
    // balanced lets WebRTC trade both resolution and frame rate instead of
    // holding 720p until latency becomes visibly bad.
    params.degradationPreference='balanced';
    await sender.setParameters(params);
    if(currentCall===call){
      call.appliedVideoTier=tier;
      call.appliedVideoBitrate=Math.round(maxBitrate);
      call.lastVideoBudgetChangeAt=Date.now();
      if(previousTier!==tier)call.lastVideoTierChangeAt=Date.now()
    }
  }catch{}
}

async function maybeAdaptPrivateVideo(call,network){
  if(!call||currentCall!==call||!call.video||call.cameraOff)return;
  const tier=privateVideoTierForNetwork(network);
  const currentTier=call.appliedVideoTier||'good';

  if(tier===currentTier){
    call.pendingVideoTier=null;
    call.pendingVideoTierSamples=0;
    const bandwidth=Number(network?.availableOutgoingBitrate);
    if(Number.isFinite(bandwidth)&&bandwidth>0){
      const profile=tier==='good'
        ?{max:2800000}
        :(tier==='fair'
          ?{max:1350000}
          :{max:560000});
      const desiredCap=Math.min(
        profile.max,
        Math.max(90000,bandwidth-105000)
      );
      const currentCap=Number(call.appliedVideoBitrate)||0;
      const budgetOldEnough=
        !call.lastVideoBudgetChangeAt
        ||Date.now()-call.lastVideoBudgetChangeAt>=4000;
      if(
        budgetOldEnough
        &&(
          !currentCap
          ||Math.abs(desiredCap-currentCap)>=Math.max(100000,currentCap*0.25)
        )
      ){
        await applyPrivateVideoTier(call,tier,network)
      }
    }
    return
  }

  if(call.pendingVideoTier===tier){
    call.pendingVideoTierSamples=(call.pendingVideoTierSamples||0)+1
  }else{
    call.pendingVideoTier=tier;
    call.pendingVideoTierSamples=1
  }

  const upgrading=networkTierRank(tier)>networkTierRank(currentTier);
  const requiredSamples=upgrading
    ?5
    :(severePrivateNetworkProblem(network)?1:2);
  if((call.pendingVideoTierSamples||0)<requiredSamples)return;

  const minDelay=upgrading?8000:2200;
  if(call.lastVideoTierChangeAt&&Date.now()-call.lastVideoTierChangeAt<minDelay)return;

  call.pendingVideoTier=null;
  call.pendingVideoTierSamples=0;
  await applyPrivateVideoTier(call,tier,network)
}

async function applyPrivateAudioTier(call,tier){
  if(!call||currentCall!==call||!call.pc)return;
  const sender=call.pc.getSenders().find(item=>item.track?.kind==='audio');
  if(!sender?.track||typeof sender.getParameters!=='function'||typeof sender.setParameters!=='function')return;

  const bitrates={
    good:48000,
    fair:40000,
    poor:32000
  };
  const maxBitrate=bitrates[tier]||bitrates.good;

  try{
    const params=sender.getParameters();
    if(!Array.isArray(params.encodings)||!params.encodings.length){
      params.encodings=[{}]
    }
    for(const encoding of params.encodings){
      encoding.maxBitrate=maxBitrate
    }
    await sender.setParameters(params);
    if(currentCall===call){
      call.appliedAudioTier=tier;
      call.lastAudioTierChangeAt=Date.now()
    }
  }catch{}
}

async function maybeAdaptPrivateAudio(call,network){
  if(!call||currentCall!==call)return;
  const tier=privateAudioTierForNetwork(network);

  if(tier===call.appliedAudioTier){
    call.pendingAudioTier=null;
    call.pendingAudioTierSamples=0;
    return
  }

  if(call.pendingAudioTier===tier){
    call.pendingAudioTierSamples=(call.pendingAudioTierSamples||0)+1
  }else{
    call.pendingAudioTier=tier;
    call.pendingAudioTierSamples=1
  }

  const currentTier=call.appliedAudioTier||'good';
  const upgrading=networkTierRank(tier)>networkTierRank(currentTier);
  const requiredSamples=upgrading?5:3;
  if((call.pendingAudioTierSamples||0)<requiredSamples)return;
  if(call.lastAudioTierChangeAt&&Date.now()-call.lastAudioTierChangeAt<(upgrading?9000:5500))return;

  call.pendingAudioTier=null;
  call.pendingAudioTierSamples=0;
  await applyPrivateAudioTier(call,tier)
}

async function refreshCallPing(call){
  if(!call||currentCall!==call||call.pc?.connectionState!=='connected'){
    setCallPing(null);
    return
  }
  try{
    const network=await readPeerNetworkStats(call.pc);
    if(currentCall!==call)return;
    setCallPing(network.rttMs,network);
    call.lastNetworkStats=network;
    reportPrivateCallQuality(call,network).catch(()=>{});
    maybeAdaptPrivateAudio(call,network).catch(()=>{});
    maybeAdaptPrivateVideo(call,network).catch(()=>{})
  }catch{
    if(currentCall===call)setCallPing(null)
  }
}

function startCallPing(call){
  stopCallPing();
  if(!call||currentCall!==call)return;
  refreshCallPing(call);
  callPingTimer=setInterval(()=>refreshCallPing(call),1500)
}

async function reportPrivateCallQuality(call,network=null,endReason='',force=false){
  if(!call?.callId)return;
  const now=Date.now();
  if(!force&&now-(call.lastQualityReportAt||0)<9500)return;
  call.lastQualityReportAt=now;
  const stats=network||call.lastNetworkStats||{};
  await api('/api/calls/quality',{
    method:'POST',
    body:{
      call_key:call.callId,
      call_type:'private',
      peer_id:call.peerId||null,
      rtt_ms:Number.isFinite(Number(stats.rttMs))?Number(stats.rttMs):null,
      packet_loss_pct:Number.isFinite(Number(stats.packetLossPct))?Number(stats.packetLossPct):null,
      jitter_ms:Number.isFinite(Number(stats.jitterMs))?Number(stats.jitterMs):null,
      available_outgoing_bitrate:Number.isFinite(Number(stats.availableOutgoingBitrate))?Number(stats.availableOutgoingBitrate):null,
      video_bitrate_bps:Number.isFinite(Number(stats.videoBitrateBps))?Number(stats.videoBitrateBps):null,
      video_width:Number.isFinite(Number(stats.videoWidth))?Math.round(Number(stats.videoWidth)):null,
      video_height:Number.isFinite(Number(stats.videoHeight))?Math.round(Number(stats.videoHeight)):null,
      video_fps:Number.isFinite(Number(stats.videoFps))?Number(stats.videoFps):null,
      connection_quality:privateVideoTierForNetwork(stats),
      end_reason:endReason||null
    }
  })
}

function callVisualUrl(avatarUrl){
  return avatarUrl||'/icon-512.svg'
}

function clearRemoteVideoFrame(){
  const remote=$('remoteVideo');
  if(!remote)return;
  try{remote.pause()}catch{}
  try{remote.srcObject=null}catch{}
  remote.removeAttribute('src');
  remote.removeAttribute('poster');
  try{remote.load()}catch{}
}

function setCallVisual(name,avatarUrl){
  const fallback=!avatarUrl;
  const url=callVisualUrl(avatarUrl);
  const backdrop=$('callBackdrop');
  const avatar=$('callAvatar');

  if(backdrop){
    backdrop.src=url;
    backdrop.classList.toggle('fallback',fallback)
  }

  if(avatar){
    avatar.replaceChildren();
    const img=document.createElement('img');
    img.src=url;
    img.alt=fallback?'Свои':(name||'Аватар');
    img.onerror=()=>{
      if(img.src.endsWith('/icon-512.svg'))return;
      img.src='/icon-512.svg';
      img.alt='Свои';
      backdrop?.classList.add('fallback');
      if(backdrop){
        backdrop.src='/icon-512.svg';
        backdrop.removeAttribute('srcset')
      }
    };
    avatar.append(img)
  }
}

function setRemoteCallMuted(muted){
  const value=!!muted;
  if(currentCall)currentCall.remoteMuted=value;
  const badge=$('remoteMuteStatus');
  if(badge)badge.classList.toggle('hidden',!value);
  updateMiniCallBar()
}

function sendPrivateMuteState(sync=false){
  const call=currentCall;
  if(!call||!call.callId||!call.peerId)return false;
  try{
    wsSend({
      type:'call_mute',
      to_user_id:call.peerId,
      call_id:call.callId,
      muted:!!call.muted,
      sync:!!sync
    });
    return true
  }catch{
    return false
  }
}

function applyRemotePrivateVideoVisibility(call=currentCall){
  if(!call||currentCall!==call)return;
  const video=$('remoteVideo');
  const avatar=$('callAvatar');
  const visible=!!call.remoteVideo&&!call.remoteCameraOff;

  video?.classList.toggle('hidden',!visible);
  avatar?.classList.toggle('hidden',visible);

  if(visible){
    video?.play?.().catch(()=>{})
    exitEarMode()
  }
  applyPrivateParticipantSize();
  syncNativeProximity().catch(()=>{})
}

function setRemotePrivateVideoEnabled(enabled){
  const call=currentCall;
  if(!call)return;
  call.remoteCameraOff=!enabled;
  applyRemotePrivateVideoVisibility(call);
  updateMiniCallBar()
}

function sendPrivateVideoState(sync=false){
  const call=currentCall;
  if(!call||!call.callId||!call.peerId)return false;
  const track=localVideoTrack();
  const enabled=!!track&&!!track.enabled&&!call.cameraOff;
  try{
    wsSend({
      type:'call_video_state',
      to_user_id:call.peerId,
      call_id:call.callId,
      enabled,
      sync:!!sync
    });
    return true
  }catch{
    return false
  }
}

function updatePrivateInviteButton(){
  const button=$('privateInviteCall');
  const wrap=$('privateInviteCallWrap');
  if(!button||!wrap)return;
  const hidden=!currentCall?.answered||!!pendingCall||!!groupCallState;
  wrap.classList.toggle('hidden',hidden)
}

function showCallUi(name,status,video,incoming=false,avatarUrl=null,nativeSystemControls=false){
  clearRemoteVideoFrame();
  $('callOverlay').classList.remove('hidden');
  $('callOverlay').classList.toggle('incoming-call-state',incoming);
  $('callOverlay').classList.toggle('active-call-state',!incoming);
  $('callName').textContent=name||'Звонок';$('callStatus').textContent=status;
  setRemoteCallMuted(false);
  setCallPing(null);
  setCallVisual(name,avatarUrl);
  $('callAvatar').classList.remove('hidden');
  $('incomingActions').classList.toggle('hidden',!incoming);
  $('activeCallControls').classList.toggle('hidden',incoming);
  $('minimizeCallBtn').classList.toggle('hidden',incoming);
  updateSpeakerButtons();
  if(nativeAudioRoutePlugin()){
    applyNativeSpeakerMode(callSpeakerMode).catch(()=>{})
  }
  $('cameraCall').classList.remove('hidden');
  updatePrivateVideoControls();
  updatePrivateInviteButton()
}

function resetCallUi(){
  window.SvoiAdminMasks?.stopPrivate();
  stopCallPing();
  exitEarMode();
  hideMiniCallBar();
  stopNativeCallService().catch(()=>{});
  resetNativeAudioRoute().catch(()=>{});
  stopRingtone();
  stopOutgoingTone();
  $('callOverlay').classList.add('hidden');
  $('callOverlay').classList.remove('incoming-call-state','active-call-state');
  clearRemoteVideoFrame();$('localVideo').srcObject=null;$('localVideo').classList.remove('mirrored');
  setCallVisual('Свои',null);
  $('callAvatar').classList.remove('hidden');
  setRemoteCallMuted(false);
  $('incomingActions').classList.add('hidden');$('activeCallControls').classList.add('hidden');$('mediaPermission').classList.add('hidden');
  $('muteCall').classList.remove('off');$('cameraCall').classList.remove('off');$('cameraCall').disabled=false;updateSpeakerButtons();updatePrivateVideoControls();updatePrivateInviteButton();applyPrivateParticipantSize();
  if(preparedMediaStream){try{preparedMediaStream.getTracks().forEach(t=>t.stop())}catch{}preparedMediaStream=null}
}

function getCallAudioConstraints(){
  const supported=navigator.mediaDevices?.getSupportedConstraints?.()||{};
  const audio={
    echoCancellation:true,
    noiseSuppression:true,
    autoGainControl:true,
    channelCount:{ideal:1}
  };
  if(supported.sampleRate)audio.sampleRate={ideal:48000};
  if(supported.sampleSize)audio.sampleSize={ideal:16};
  if(supported.latency)audio.latency={ideal:0.03};
  return audio
}

function getCallVideoConstraints(facing=cameraFacing){
  return {
    facingMode:{ideal:facing},
    width:{ideal:1280,max:1280},
    height:{ideal:720,max:720},
    frameRate:{ideal:30,max:30}
  }
}


function cameraStartErrorMessage(err,fallback='Не удалось включить камеру'){
  if(err?.name==='NotAllowedError'||err?.name==='PermissionDeniedError'){
    return 'Разреши доступ к камере в настройках приложения или сайта';
  }
  if(isTransientCameraStartError(err)){
    return 'Камера не запустилась. Закрой другие приложения, использующие камеру, и попробуй снова';
  }
  return err?.message||fallback;
}
function isTransientCameraStartError(err){
  return ['NotReadableError','TrackStartError','AbortError'].includes(err?.name)
    || /could not start video source|could not start video|camera.*(busy|in use)/i.test(err?.message||'');
}
async function retryCameraStart(start,isActive=()=>true){
  for(let attempt=0;attempt<3;attempt++){
    if(!isActive())throw Object.assign(new Error('Запуск камеры отменён'),{name:'CameraCancelledError'});
    try{
      const result=await start();
      if(!isActive()){
        for(const track of result?.getTracks?.()||[])try{track.stop()}catch{}
        throw Object.assign(new Error('Запуск камеры отменён'),{name:'CameraCancelledError'});
      }
      return result;
    }catch(err){
      if(!isActive()||!isTransientCameraStartError(err)||attempt===2)throw err;
      await new Promise(resolve=>setTimeout(resolve,attempt===0?250:600));
    }
  }
}
async function getIncomingCallMedia(data){
  if(preparedMediaStream)return preparedMediaStream;
  if(incomingMediaRequest?.callId===data.call_id)return incomingMediaRequest.promise;
  const request={callId:data.call_id,promise:null};
  request.promise=getCallMedia(!!data.video,()=>pendingCall?.call_id===data.call_id)
    .then(stream=>{
      if(pendingCall?.call_id!==data.call_id){
        stream.getTracks().forEach(track=>track.stop());
        throw Object.assign(new Error('Входящий звонок завершён'),{name:'CameraCancelledError'});
      }
      preparedMediaStream=stream;
      return stream;
    }).finally(()=>{
      if(incomingMediaRequest===request)incomingMediaRequest=null;
    });
  incomingMediaRequest=request;
  return request.promise;
}
function updateGroupCameraControls(state){
  if(groupCallState!==state)return;
  $('groupCameraBtn').classList.toggle('off',state.cameraOff);
  $('groupCameraBtn').textContent=state.cameraOff?'🎥':'📷';
  $('groupCameraBtn').title=state.cameraOff?'Переключить в видеорежим':'Переключить в аудиорежим';
  $('groupSwitchCameraBtn').classList.toggle('hidden',state.cameraOff);
  $('groupCameraBtn').disabled=!!state.cameraChanging;
  $('groupSwitchCameraBtn').disabled=!!state.cameraChanging;
}

async function getCallMedia(video,isActive=()=>true){
  let mediaRequestActive=true;
  const start=()=>navigator.mediaDevices.getUserMedia({
    audio:getCallAudioConstraints(),
    video:video?getCallVideoConstraints():false
  });
  const request=video?retryCameraStart(start,()=>mediaRequestActive&&isActive()):start();
  return new Promise((resolve,reject)=>{
    let settled=false;
    const timer=setTimeout(()=>{
      settled=true;
      mediaRequestActive=false;
      const err=new Error(video?'Камера не ответила. Проверь разрешение камеры.':'Микрофон не ответил. Проверь разрешение.');
      err.name='MediaTimeoutError';reject(err)
    },12000);
    request.then(stream=>{
      if(settled){
        // getUserMedia cannot be cancelled while permission/capture is pending.
        // Release every track if it arrives after our caller timed out.
        for(const track of stream.getTracks()){
          try{track.stop()}catch{}
        }
        return
      }
      settled=true;
      clearTimeout(timer);
      resolve(stream)
    },err=>{
      if(settled)return;
      settled=true;
      clearTimeout(timer);
      reject(err)
    })
  })
}

function clearPrivateRecovery(call){
  if(!call)return;
  if(call.recoveryTimer){
    clearTimeout(call.recoveryTimer);
    call.recoveryTimer=null
  }
  call.recovering=false;
  call.recoveryAttempts=0
}

async function waitForStableSignaling(pc,timeoutMs=3500){
  const started=Date.now();
  while(pc&&pc.signalingState!=='stable'&&Date.now()-started<timeoutMs){
    await new Promise(resolve=>setTimeout(resolve,180))
  }
  return !!pc&&pc.signalingState==='stable'
}

async function recoverPrivateCallConnection(call){
  if(!call||currentCall!==call||!call.answered||call.pc?.signalingState==='closed')return;
  if(call.recovering)return;
  if((call.recoveryAttempts||0)>=5){
    $('callStatus').textContent='Связь потеряна';
    updateMiniCallBar();
    return
  }

  call.recovering=true;
  call.recoveryAttempts=(call.recoveryAttempts||0)+1;
  const attempt=call.recoveryAttempts;
  $('callStatus').textContent='Восстанавливаем связь…';
  updateMiniCallBar();

  try{
    // Небольшая прогрессивная пауза даёт браузеру/Android время
    // самостоятельно восстановить маршрут после переключения сети.
    if(attempt>1){
      await new Promise(resolve=>setTimeout(resolve,Math.min(2600,500*attempt)))
    }
    if(currentCall!==call||call.pc?.signalingState==='closed')return;

    await waitForSocketOpen(5500);
    if(currentCall!==call)return;

    if(call.pc.connectionState==='connected'){
      clearPrivateRecovery(call);
      $('callStatus').textContent='Соединено';
      updateMiniCallBar();
      return
    }

    const stable=await waitForStableSignaling(call.pc,3200);
    if(!stable)throw new Error('Сигнализация WebRTC занята');
    if(currentCall!==call)return;

    try{call.pc.restartIce?.()}catch{}
    const offer=await call.pc.createOffer({iceRestart:true});
    if(currentCall!==call)return;
    await call.pc.setLocalDescription(offer);
    await waitForSocketOpen(5500);
    if(currentCall!==call)return;

    wsSend({
      type:'call_video_offer',
      to_user_id:call.peerId,
      call_id:call.callId,
      video:!!call.video,
      reconnect:true,
      sdp:call.pc.localDescription.toJSON
        ?call.pc.localDescription.toJSON()
        :call.pc.localDescription
    });

    if(call.recoveryTimer)clearTimeout(call.recoveryTimer);
    call.recoveryTimer=setTimeout(()=>{
      if(
        currentCall===call
        && call.pc
        && !['connected','closed'].includes(call.pc.connectionState)
      ){
        call.recovering=false;
        recoverPrivateCallConnection(call).catch(()=>{})
      }
    },5000)
  }catch{
    call.recovering=false;
    if(currentCall===call&&(call.recoveryAttempts||0)<5){
      const retryDelay=Math.min(2600,650+450*(call.recoveryAttempts||0));
      if(call.recoveryTimer)clearTimeout(call.recoveryTimer);
      call.recoveryTimer=setTimeout(()=>{
        if(currentCall!==call)return;
        call.recoveryTimer=null;
        recoverPrivateCallConnection(call).catch(()=>{})
      },retryDelay)
    }
  }
}

function schedulePrivateCallRecovery(call,delay=900){
  if(!call||currentCall!==call||!call.answered)return;
  if(call.recoveryTimer)return;
  $('callStatus').textContent='Восстанавливаем связь…';
  updateMiniCallBar();
  call.recoveryTimer=setTimeout(()=>{
    if(currentCall!==call)return;
    call.recoveryTimer=null;
    recoverPrivateCallConnection(call).catch(()=>{})
  },delay)
}

async function createPeer(peerId,peerName,id,video,stream){
  const config=await getRtcConfig();
  const pc=takePrewarmedPrivateCall(peerId)||new RTCPeerConnection(config);
  currentCall={pc,peerId,peerName,callId:id,video,localStream:stream,muted:false,remoteMuted:false,cameraOff:!video,remoteCameraOff:!video,remoteVideo:false,remoteMediaStream:null,renegotiating:false,answered:false,connectedAt:0,recovering:false,recoveryTimer:null,recoveryAttempts:0,lastConnectionState:'new',appliedVideoTier:null,appliedVideoBitrate:0,pendingVideoTier:null,pendingVideoTierSamples:0,lastVideoTierChangeAt:0,lastVideoBudgetChangeAt:0,appliedAudioTier:null,pendingAudioTier:null,pendingAudioTierSamples:0,lastAudioTierChangeAt:0,lastQualityReportAt:0,lastNetworkStats:null};
  startNativeCallService(peerName,!!video,false).catch(()=>{});
  for(const track of stream.getTracks()){
    try{
      if('contentHint' in track)track.contentHint=track.kind==='audio'?'speech':'motion'
    }catch{}
    pc.addTrack(track,stream)
  }
  applyPrivateAudioTier(currentCall,'good').catch(()=>{});
  if(video)applyPrivateVideoTier(currentCall,'good').catch(()=>{});
  $('localVideo').srcObject=stream;
  updatePrivateLocalMirror();
  updatePrivateVideoControls();
  pc.ontrack=e=>{
    const call=currentCall;
    if(!call||call.pc!==pc)return;

    const incoming=e.streams?.[0]||null;
    if(incoming){
      call.remoteMediaStream=incoming;
      if($('remoteVideo').srcObject!==incoming){
        $('remoteVideo').srcObject=incoming
      }
      applyAudioOutput($('remoteVideo')).catch(()=>{});
      $('remoteVideo').play().catch(()=>{})
    }else if(e.track){
      if(!call.remoteMediaStream){
        call.remoteMediaStream=new MediaStream()
      }
      if(!call.remoteMediaStream.getTracks().some(track=>track.id===e.track.id)){
        call.remoteMediaStream.addTrack(e.track)
      }
      if($('remoteVideo').srcObject!==call.remoteMediaStream){
        $('remoteVideo').srcObject=call.remoteMediaStream
      }
      applyAudioOutput($('remoteVideo')).catch(()=>{});
      $('remoteVideo').play().catch(()=>{})
    }

    if(e.track?.kind==='video'){
      currentCall.remoteVideo=true;
      applyRemotePrivateVideoVisibility(currentCall);
      e.track.onended=()=>{
        if(currentCall){
          currentCall.remoteVideo=false;
          applyRemotePrivateVideoVisibility(currentCall)
        }
      }
    }
    $('callStatus').textContent='Соединено'
  };
  pc.onicecandidate=e=>{
    if(!e.candidate)return;
    const candidate=e.candidate.toJSON?e.candidate.toJSON():e.candidate;
    const sendCandidate=()=>{
      const call=currentCall;
      if(!call||call.pc!==pc||call.callId!==id)return;
      wsSend({type:'ice_candidate',to_user_id:peerId,call_id:id,candidate})
    };
    if(socket?.readyState===WebSocket.OPEN){
      try{sendCandidate()}catch{}
    }else{
      waitForSocketOpen(5000).then(()=>{
        try{sendCandidate()}catch{}
      }).catch(()=>{})
    }
  };
  pc.onconnectionstatechange=()=>{
    const call=currentCall;
    if(!call||call.pc!==pc)return;
    const state=pc.connectionState;
    if(call.lastConnectionState===state)return;
    call.lastConnectionState=state;

    if(state==='connected'){
      call.answered=true;
      if(!call.connectedAt)call.connectedAt=Date.now();
      ensureLiveCallDurationTimer();
      clearPrivateRecovery(call);
      startCallPing(call);
      applyPrivateAudioTier(call,call.appliedAudioTier||'good').catch(()=>{});
      stopOutgoingTone();
      $('callStatus').textContent='Соединено';
      updateMiniCallBar();
      updateEarModeButton();
      updatePrivateInviteButton()
    }else if(state==='connecting'){
      stopCallPing();
      $('callStatus').textContent='Соединение…';
      updateMiniCallBar()
    }else if(state==='disconnected'){
      stopCallPing();
      // Короткий обрыв WebRTC часто восстанавливает сам.
      schedulePrivateCallRecovery(call,1400)
    }else if(state==='failed'){
      stopCallPing();
      schedulePrivateCallRecovery(call,180)
    }else if(state==='closed'){
      stopCallPing();
      clearPrivateRecovery(call);
      finishCall(false)
    }
  };
  return pc
}

async function flushPendingIce(){
  if(!currentCall?.pc?.remoteDescription)return;
  const list=pendingIce.splice(0);
  for(const candidate of list){
    try{await currentCall.pc.addIceCandidate(new RTCIceCandidate(candidate))}catch{}
  }
}

async function startCallTo(u,video){
  if(!u||currentCall||pendingCall)return;
  if(!navigator.mediaDevices?.getUserMedia){alert('Браузер не поддерживает звонки');return}
  try{
    await waitForSocketOpen();
    const stream=await getCallMedia(video);
    const id=callId();pendingIce=[];
    const pc=await createPeer(u.id,u.display_name,id,video,stream);
    showCallUi(u.display_name,'Вызов…',video,false,u.avatar_url||null,false);
    const offer=await pc.createOffer();
    await pc.setLocalDescription(offer);
    wsSend({type:'call_offer',to_user_id:u.id,call_id:id,video,sdp:pc.localDescription.toJSON?pc.localDescription.toJSON():pc.localDescription});
    startOutgoingTone().catch(()=>{})
  }catch(err){
    finishCall(false);
    alert(err.name==='NotAllowedError'?'Нужен доступ к микрофону/камере':(video?cameraStartErrorMessage(err,'Не удалось начать видеозвонок'):(err.message||'Не удалось начать звонок')))
  }
}

async function startCall(video){
  if(active?.type!=='user')return;
  return startCallTo(active.data,video)
}

function incomingCall(data,suppressRingtone=false,nativeSystemControls=false){
  if(currentCall?.callId===data.call_id||pendingCall?.call_id===data.call_id){
    if(Array.isArray(data.ice_candidates)){
      pendingIce.push(...data.ice_candidates)
    }
    return
  }
  if(currentCall||pendingCall||groupCallState||groupCallJoining){
    try{wsSend({type:'call_reject',to_user_id:data.from_user_id,call_id:data.call_id})}catch{}
    return
  }
  pendingIce=Array.isArray(data.ice_candidates)
    ?data.ice_candidates.slice()
    :[];
  pendingCall=data;preparedMediaStream=null;
  closePrewarmedPrivateCall();
  getRtcConfig().catch(()=>{});
  showCallUi(
    data.from_name,
    data.video?'Входящий видеозвонок':'Входящий голосовой звонок',
    data.video,
    true,
    data.from_avatar_url||null,
    nativeSystemControls
  );
  if(!suppressRingtone)startRingtone().catch(()=>{});
  $('mediaPermission').classList.toggle('hidden',!data.video||!!data.conference_invitation);
  if(data.conference_invitation)$('callStatus').textContent='Приглашение: '+(data.conference_name||'Конференция');
  if(data.video&&!data.conference_invitation){
    $('mediaPermissionText').textContent='Нужен доступ к камере и микрофону';
    $('mediaPermissionBtn').textContent='Разрешить камеру и микрофон';
    $('mediaPermissionBtn').disabled=false
  }
}

async function requestIncomingMediaPermission(){
  if(!pendingCall?.video||acceptingCall||$('mediaPermissionBtn').disabled)return;
  const data=pendingCall;
  $('mediaPermissionBtn').disabled=true;
  $('mediaPermissionText').textContent='Запрашиваем доступ…';
  try{
    const stream=await getIncomingCallMedia(data);
    if(pendingCall?.call_id!==data.call_id||acceptingCall)return;
    $('mediaPermissionText').textContent='Доступ получен — нажми ✓';
    $('mediaPermissionBtn').textContent='Камера разрешена ✅';
    $('localVideo').srcObject=stream;$('localVideo').classList.remove('hidden');
    updatePrivateLocalMirror();
    $('localVideo').play().catch(()=>{})
  }catch(err){
    if(pendingCall?.call_id!==data.call_id||acceptingCall)return;
    $('mediaPermissionBtn').disabled=false;
    $('mediaPermissionBtn').textContent='Попробовать снова';
    if(err.name==='NotAllowedError'){
      $('mediaPermissionText').textContent='Камера/микрофон запрещены в настройках браузера для этого сайта'
    }else{
      $('mediaPermissionText').textContent=cameraStartErrorMessage(err,'Не удалось открыть камеру')
    }
  }
}

$('mediaPermissionBtn').onclick=requestIncomingMediaPermission;

async function acceptIncomingCall(options={}){
  if(!pendingCall||acceptingCall)return false;
  if(pendingCall.conference_invitation)return acceptIncomingConferenceInvitation(options);
  const nativeResume=!!options.nativeResume;
  const suppressFailureAlert=!!options.suppressFailureAlert;
  stopRingtone();
  acceptingCall=true;
  const data=pendingCall;
  clearNativeCallNotification(data.call_id);
  $('acceptCall').disabled=true;$('rejectCall').disabled=true;
  showCallUi(
    data.from_name,
    data.video?'Подключаем камеру…':'Подключаем микрофон…',
    !!data.video,
    false,
    data.from_avatar_url||null,
    false
  );
  try{
    const stream=await getIncomingCallMedia(data);
    preparedMediaStream=null;
    if(!pendingCall||pendingCall.call_id!==data.call_id){
      stream.getTracks().forEach(t=>t.stop());
      return currentCall?.callId===data.call_id
    }
    const pc=await createPeer(
      data.from_user_id,
      data.from_name,
      data.call_id,
      !!data.video,
      stream
    );
    setRemoteCallMuted(!!data.remote_muted);
    setRemotePrivateVideoEnabled(
      data.remote_video_enabled==null
        ?!!data.video
        :!!data.remote_video_enabled
    );
    pendingCall=null;
    $('mediaPermission').classList.add('hidden');
    $('callStatus').textContent='Соединение…';
    await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
    await flushPendingIce();
    const answer=await pc.createAnswer();
    await pc.setLocalDescription(answer);
    await waitForSocketOpen();
    wsSend({
      type:'call_answer',
      to_user_id:data.from_user_id,
      call_id:data.call_id,
      video:!!data.video,
      sdp:pc.localDescription.toJSON?pc.localDescription.toJSON():pc.localDescription
    });
    if(currentCall){
      currentCall.answered=true;
      $('incomingActions').classList.add('hidden');
      $('activeCallControls').classList.remove('hidden');
      $('callOverlay').classList.remove('incoming-call-state');
      $('callOverlay').classList.add('active-call-state');
      updateMiniCallBar();
      updateEarModeButton();
      updatePrivateInviteButton()
    }
    return true
  }catch(err){
    if(nativeResume){
      const failed=currentCall?.callId===data.call_id?currentCall:null;
      if(failed){
        clearPrivateRecovery(failed);
        try{failed.pc.onicecandidate=null;failed.pc.ontrack=null;failed.pc.close()}catch{}
        try{failed.localStream?.getTracks().forEach(t=>t.stop())}catch{}
        if(currentCall===failed)currentCall=null
      }
      if(preparedMediaStream){
        try{preparedMediaStream.getTracks().forEach(t=>t.stop())}catch{}
        preparedMediaStream=null
      }
      pendingCall=data;
      $('incomingActions').classList.remove('hidden');
      $('activeCallControls').classList.add('hidden');
      $('callStatus').textContent='Подключаем звонок…';
      if(!suppressFailureAlert){
        alert(err?.message||'Не удалось принять звонок')
      }
      return false
    }

    try{wsSend({type:'call_reject',to_user_id:data.from_user_id,call_id:data.call_id})}catch{}
    finishCall(false);
    const message=err.name==='NotAllowedError'
      ?'Нужен доступ к микрофону и камере'
      :(data.video?cameraStartErrorMessage(err,'Не удалось принять видеозвонок'):(err.message||'Не удалось принять звонок'));
    alert(message);
    return false
  }finally{
    acceptingCall=false;
    $('acceptCall').disabled=false;$('rejectCall').disabled=false
  }
}

async function rejectIncomingCall(){
  if(!pendingCall||acceptingCall)return;
  const data=pendingCall;
  clearNativeCallNotification(data.call_id);
  try{
    await waitForSocketOpen();
    wsSend({type:'call_reject',to_user_id:data.from_user_id,call_id:data.call_id})
  }catch{}
  pendingCall=null;pendingIce=[];resetCallUi();
  setTimeout(()=>loadCallHistory().catch(()=>{}),500)
}


function closePrivateCallForConference(){
  const call=currentCall;
  if(!call)return null;
  reportPrivateCallQuality(call,call.lastNetworkStats,'promoted_to_conference',true).catch(()=>{});
  clearPrivateRecovery(call);
  stopCallPing();
  stopOutgoingTone();
  try{
    call.pc.onicecandidate=null;
    call.pc.ontrack=null;
    call.pc.close()
  }catch{}
  try{call.localStream?.getTracks().forEach(track=>track.stop())}catch{}
  currentCall=null;
  pendingCall=null;
  pendingIce=[];
  acceptingCall=false;
  resetCallUi();
  return call
}

async function promotePrivateCallToConference(inviteToken,video=false){
  const call=currentCall;
  if(!call?.answered)throw new Error('Личный звонок уже завершён');
  const callId=call.callId;
  const callVideo=!!(video||call.video);
  closePrivateCallForConference();
  await new Promise(resolve=>setTimeout(resolve,250));
  try{
    const joined=await joinGroupCall(null,callVideo,false,inviteToken);
    if(!joined)throw new Error('Не удалось перейти в конференцию');
    return {callId,video:callVideo}
  }catch(err){
    throw err
  }
}

function finishCall(notify=true,endReason='local_hangup'){
  const endingCallId=currentCall?.callId||pendingCall?.call_id||null;
  if(endingCallId)clearNativeCallNotification(endingCallId);
  if(notify&&currentCall){
    try{wsSend({type:'call_end',to_user_id:currentCall.peerId,call_id:currentCall.callId})}catch{}
  }else if(notify&&pendingCall){
    try{wsSend({type:'call_reject',to_user_id:pendingCall.from_user_id,call_id:pendingCall.call_id})}catch{}
  }
  if(currentCall){
    const finishingCall=currentCall;
    reportPrivateCallQuality(
      finishingCall,
      finishingCall.lastNetworkStats,
      endReason,
      true
    ).catch(()=>{});
    clearPrivateRecovery(finishingCall);
    try{finishingCall.pc.onicecandidate=null;finishingCall.pc.ontrack=null;finishingCall.pc.close()}catch{}
    try{finishingCall.localStream?.getTracks().forEach(t=>t.stop())}catch{}
  }
  acceptingCall=false;currentCall=null;pendingCall=null;pendingIce=[];
  stopLiveCallDurationTimerIfIdle();
  resetCallUi();
  syncNativeProximity().catch(()=>{});
  setTimeout(()=>loadCallHistory().catch(()=>{}),500)
}

function enqueueCallSignal(data){
  callSignalChain=callSignalChain
    .then(()=>handleCallSignal(data))
    .catch(err=>console.warn('Call signaling error',err));
  return callSignalChain
}

async function handleCallSignal(data){
  if(data.type==='call_offer'){incomingCall(data);return}
  if(data.type==='call_mute'){
    if(
      currentCall
      &&currentCall.callId===data.call_id
      &&Number(currentCall.peerId)===Number(data.from_user_id)
    ){
      setRemoteCallMuted(!!data.muted)
    }else if(
      pendingCall
      &&pendingCall.call_id===data.call_id
      &&Number(pendingCall.from_user_id)===Number(data.from_user_id)
    ){
      pendingCall.remote_muted=!!data.muted
    }
    return
  }
  if(data.type==='call_video_state'){
    if(
      currentCall
      &&currentCall.callId===data.call_id
      &&Number(currentCall.peerId)===Number(data.from_user_id)
    ){
      setRemotePrivateVideoEnabled(!!data.enabled)
    }else if(
      pendingCall
      &&pendingCall.call_id===data.call_id
      &&Number(pendingCall.from_user_id)===Number(data.from_user_id)
    ){
      pendingCall.remote_video_enabled=!!data.enabled
    }
    return
  }
  if(data.type==='call_unavailable'){
    if(currentCall&&currentCall.callId===data.call_id){stopOutgoingTone();alert('Пользователь сейчас недоступен');finishCall(false)}
    return
  }
  if(data.type==='call_reject'){
    if(currentCall&&currentCall.callId===data.call_id){stopOutgoingTone();$('callStatus').textContent='Звонок отклонён';setTimeout(()=>finishCall(false,'rejected'),900)}
    return
  }
  if(data.type==='call_end'){
    if((currentCall&&currentCall.callId===data.call_id)||(pendingCall&&pendingCall.call_id===data.call_id)){finishCall(false,'remote_hangup')}
    return
  }
  if(data.type==='ice_candidate'){
    if(currentCall&&currentCall.callId===data.call_id&&currentCall.pc.remoteDescription){
      try{await currentCall.pc.addIceCandidate(new RTCIceCandidate(data.candidate))}catch{}
    }else if((currentCall&&currentCall.callId===data.call_id)||(pendingCall&&pendingCall.call_id===data.call_id)){
      pendingIce.push(data.candidate)
    }
    return
  }
  if(data.type==='call_video_offer'){
    if(!currentCall||currentCall.callId!==data.call_id)return;
    try{
      if(currentCall.pc.signalingState==='have-local-offer'){
        await currentCall.pc.setLocalDescription({type:'rollback'})
      }
      await currentCall.pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
      const answer=await currentCall.pc.createAnswer();
      await currentCall.pc.setLocalDescription(answer);
      await waitForSocketOpen();
      wsSend({
        type:'call_video_answer',
        to_user_id:data.from_user_id,
        call_id:data.call_id,
        video:!!data.video,
        reconnect:!!data.reconnect,
        sdp:currentCall.pc.localDescription.toJSON
          ?currentCall.pc.localDescription.toJSON()
          :currentCall.pc.localDescription
      });
      currentCall.video=!!data.video;
      if(data.video)setRemotePrivateVideoEnabled(true);
      currentCall.answered=true;
      clearPrivateRecovery(currentCall);
      $('callStatus').textContent='Соединено';
      updateMiniCallBar()
    }catch(err){
      $('callStatus').textContent='Ошибка подключения видео'
    }
    return
  }
  if(data.type==='call_video_answer'){
    if(!currentCall||currentCall.callId!==data.call_id)return;
    try{
      if(currentCall.pc.signalingState==='have-local-offer'){
        await currentCall.pc.setRemoteDescription(new RTCSessionDescription(data.sdp))
      }
      currentCall.video=!!data.video;
      sendPrivateVideoState(false);
      clearPrivateRecovery(currentCall);
      $('callStatus').textContent='Соединено';
      updateMiniCallBar()
    }catch(err){
      $('callStatus').textContent='Ошибка подключения видео'
    }
    return
  }
  if(data.type==='call_answer'){
    if(!currentCall||currentCall.callId!==data.call_id)return;
    const call=currentCall;
    stopOutgoingTone();
    call.answered=true;
    sendPrivateMuteState(true);
    sendPrivateVideoState(true);
    updateMiniCallBar();
    updateEarModeButton();
    updatePrivateInviteButton();

    // Cold-start Android acceptance can deliver the same answer more than
    // once through reconnect/restore paths. A duplicate answer in stable
    // signaling state is harmless and must not terminate the call.
    if(
      call.pc.signalingState==='stable'
      && call.pc.remoteDescription?.type==='answer'
    ){
      $('callStatus').textContent=call.pc.connectionState==='connected'
        ?'Соединено'
        :'Соединение…';
      return
    }

    try{
      if(call.pc.signalingState==='have-local-offer'){
        await call.pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
        await flushPendingIce()
      }
      $('callStatus').textContent='Соединение…'
    }catch(err){
      if(currentCall!==call)return;
      $('callStatus').textContent='Восстанавливаем связь…';
      schedulePrivateCallRecovery(call,900)
    }
  }
}

$('audioCallBtn').onclick=()=>{
  if(active?.type==='group')joinGroupCall(active.data.id,false,true);
  else if(!isActiveUserBlocked())startCall(false)
};
$('videoCallBtn').onclick=()=>{
  if($('chatMenuDialog').open)$('chatMenuDialog').close();
  if(active?.type==='group')joinGroupCall(active.data.id,true,true);
  else if(!isActiveUserBlocked())startCall(true)
};
$('acceptCall').onclick=acceptIncomingCall;
$('rejectCall').onclick=rejectIncomingCall;
$('endCall').onclick=()=>finishCall(true);
$('minimizeCallBtn').onclick=()=>minimizePrivateCall();
$('restoreMiniCall').onclick=()=>restoreMinimizedCall();
$('miniCallEnd').onclick=()=>{
  if(minimizedCallKind==='group')leaveGroupCall(true);
  else if(currentCall||pendingCall)finishCall(true)
};

$('muteCall').onclick=()=>{
  if(!currentCall)return;
  currentCall.muted=!currentCall.muted;
  currentCall.localStream.getAudioTracks().forEach(t=>t.enabled=!currentCall.muted);
  $('muteCall').classList.toggle('off',currentCall.muted);
  $('muteCall').textContent=currentCall.muted?'🔇':'🎤';
  sendPrivateMuteState(false)
};

$('speakerCall').onclick=()=>chooseSpeakerOutput();
$('privateSizeDown').onclick=()=>changePrivateParticipantSize(-1);
$('privateSizeUp').onclick=()=>changePrivateParticipantSize(1);
$('earModeBtn').onclick=()=>enterEarMode();
const earLock=$('earLock');
earLock.addEventListener('pointerdown',beginEarUnlock);
earLock.addEventListener('pointerup',cancelEarUnlock);
earLock.addEventListener('pointercancel',cancelEarUnlock);
earLock.addEventListener('pointerleave',event=>{
  if(event.pointerType!=='touch')cancelEarUnlock()
});
earLock.addEventListener('contextmenu',event=>event.preventDefault());

bindPrivateLocalVideoDrag();

$('cameraCall').onclick=async()=>{
  if(!currentCall)return;
  const track=localVideoTrack();
  if(!track){
    await upgradeCurrentCallToVideo();
    return
  }

  currentCall.cameraOff=track.enabled;
  track.enabled=!currentCall.cameraOff;
  if(!currentCall.cameraOff)exitEarMode();
  updatePrivateVideoControls();
  sendPrivateVideoState(false);
  startNativeCallService(
    currentCall.peerName,
    !currentCall.cameraOff,
    false
  ).catch(()=>{})
};

$('switchCamera').onclick=async()=>{
  if(!currentCall||!localVideoTrack())return;
  const button=$('switchCamera');
  if(button.disabled)return;
  button.disabled=true;

  const call=currentCall;
  const isActive=()=>currentCall===call&&call.pc?.signalingState!=='closed';
  const pendingStreams=new Set();
  const openCamera=async constraints=>{
    const stream=await retryCameraStart(()=>navigator.mediaDevices.getUserMedia(constraints),isActive);
    pendingStreams.add(stream);
    return stream
  };
  let oldTrackRemoved=false;
  const oldTrack=call.localStream.getVideoTracks()[0];
  const oldFacing=cameraFacing;
  const nextFacing=oldFacing==='user'?'environment':'user';

  try{
    let devices=[];
    try{
      devices=(await navigator.mediaDevices.enumerateDevices())
        .filter(device=>device.kind==='videoinput')
    }catch{}

    if(!isActive())return;
    if(devices.length===1){
      throw new Error('Браузер видит только одну камеру')
    }

    const oldSettings=oldTrack?.getSettings?.()||{};
    const currentDeviceId=oldSettings.deviceId||'';
    let targetDevice=null;

    if(devices.length>1){
      targetDevice=devices.find(device=>
        device.deviceId &&
        device.deviceId!==currentDeviceId &&
        (
          nextFacing==='environment'
            ? /back|rear|environment|зад/i.test(device.label||'')
            : /front|user|face|перед/i.test(device.label||'')
        )
      ) || devices.find(device=>
        device.deviceId && device.deviceId!==currentDeviceId
      )
    }

    if(oldTrack){
      oldTrack.enabled=false;
      oldTrack.stop();
      call.localStream.removeTrack(oldTrack);
      oldTrackRemoved=true;
      await new Promise(resolve=>setTimeout(resolve,200));
      if(!isActive())return
    }

    let constraints;
    if(targetDevice?.deviceId){
      constraints={video:{...getCallVideoConstraints(nextFacing),deviceId:{exact:targetDevice.deviceId}},audio:false}
    }else{
      constraints={video:{...getCallVideoConstraints(nextFacing),facingMode:{exact:nextFacing}},audio:false}
    }

    let newStream;
    try{
      newStream=await openCamera(constraints)
    }catch(firstError){
      if(!isActive())return;
      if(['NotAllowedError','PermissionDeniedError'].includes(firstError?.name))throw firstError;
      newStream=await openCamera({
        video:getCallVideoConstraints(nextFacing),
        audio:false
      })
    }

    if(!isActive())return;
    const newTrack=newStream.getVideoTracks()[0];
    if(!newTrack)throw new Error('Новая камера не открылась');

    const sender=call.pc.getSenders().find(s=>s.track?.kind==='video');
    if(!sender)throw new Error('Видеотрек звонка не найден');

    await sender.replaceTrack(newTrack);
    if(!isActive())return;
    newTrack.enabled=!call.cameraOff;
    call.localStream.addTrack(newTrack);
    pendingStreams.delete(newStream);
    cameraFacing=nextFacing;

    $('localVideo').srcObject=call.localStream;
    updatePrivateLocalMirror();
    $('localVideo').play().catch(()=>{})
  }catch(err){
    if(!isActive())return;
    for(const stream of pendingStreams){
      stream.getTracks().forEach(track=>track.stop())
    }
    pendingStreams.clear();
    try{
      if(!oldTrackRemoved)throw err;
      const restore=await openCamera({
        video:getCallVideoConstraints(oldFacing),
        audio:false
      });
      if(!isActive())return;
      const restoreTrack=restore.getVideoTracks()[0];
      const sender=call.pc.getSenders().find(s=>s.track?.kind==='video');
      if(sender&&restoreTrack){
        await sender.replaceTrack(restoreTrack);
        if(!isActive())return;
        restoreTrack.enabled=!call.cameraOff;
        call.localStream.addTrack(restoreTrack);
        pendingStreams.delete(restore);
        cameraFacing=oldFacing;
        $('localVideo').srcObject=call.localStream;
        updatePrivateLocalMirror();
        $('localVideo').play().catch(()=>{})
      }
    }catch{}
    if(isActive())alert(cameraStartErrorMessage(err,'Не удалось переключить камеру'))
  }finally{
    for(const stream of pendingStreams){
      stream.getTracks().forEach(track=>track.stop())
    }
    button.disabled=false
  }
};

function waitForSocketOpen(timeoutMs=6000){
  if(socket?.readyState===WebSocket.OPEN)return Promise.resolve();
  return new Promise((resolve,reject)=>{
    const started=Date.now();
    const timer=setInterval(()=>{
      if(socket?.readyState===WebSocket.OPEN){
        clearInterval(timer);resolve();return
      }
      if(Date.now()-started>=timeoutMs){
        clearInterval(timer);reject(new Error('Нет соединения с сервером'))
      }
    },80)
  })
}

async function waitForAppForeground(timeoutMs=10000){
  if(document.visibilityState==='visible'){
    await new Promise(resolve=>setTimeout(resolve,250));
    return
  }
  await new Promise((resolve,reject)=>{
    const started=Date.now();
    const onChange=()=>{
      if(document.visibilityState==='visible'){
        cleanup();
        setTimeout(resolve,250)
      }
    };
    const timer=setInterval(()=>{
      if(document.visibilityState==='visible'){
        cleanup();
        setTimeout(resolve,250)
      }else if(Date.now()-started>=timeoutMs){
        cleanup();
        reject(new Error('Приложение ещё не готово к звонку'))
      }
    },120);
    const cleanup=()=>{
      clearInterval(timer);
      document.removeEventListener('visibilitychange',onChange)
    };
    document.addEventListener('visibilitychange',onChange)
  })
}

function incomingActionFromLocation(){
  const params=new URLSearchParams(location.search);
  const callId=params.get('incoming_call');
  if(!callId)return null;
  return {
    callId,
    nativeRing:params.get('native_ring')==='1',
    nativeAccept:params.get('native_accept')==='1'
  }
}

function savePendingNativeCallAction(action){
  if(!action?.callId)return;
  try{
    sessionStorage.setItem(
      'svoi_pending_native_call',
      JSON.stringify({...action,savedAt:Date.now()})
    )
  }catch{}
}

function readPendingNativeCallAction(){
  try{
    const raw=sessionStorage.getItem('svoi_pending_native_call');
    if(!raw)return null;
    const action=JSON.parse(raw);
    if(!action?.callId||Date.now()-Number(action.savedAt||0)>60000){
      sessionStorage.removeItem('svoi_pending_native_call');
      return null
    }
    return action
  }catch{return null}
}

function clearPendingNativeCallAction(){
  try{sessionStorage.removeItem('svoi_pending_native_call')}catch{}
}

let nativeAcceptRestoreCallId=null;

async function restoreIncomingCallAction(action){
  if(!action?.callId)return;
  const callId=String(action.callId);
  const nativeSystemControls=!!(action.nativeRing||action.nativeAccept);

  if(action.nativeAccept){
    if(nativeAcceptRestoreCallId===callId)return;
    nativeAcceptRestoreCallId=callId
  }

  try{
    if(nativeSystemControls){
      $('incomingActions')?.classList.remove('hidden');
    }

    await waitForAppForeground();
    await waitForSocketOpen(12000);

    let data=null;
    try{
      data=await api('/api/calls/pending/'+encodeURIComponent(callId))
    }catch(err){
      if(
        err?.status===404
        && pendingCall?.call_id===callId
      ){
        data=pendingCall
      }else if(currentCall?.callId===callId){
        clearPendingNativeCallAction();
        history.replaceState({},'',location.pathname);
        return
      }else{
        throw err
      }
    }

    if(data&&pendingCall?.call_id!==callId&&currentCall?.callId!==callId){
      incomingCall(
        data,
        nativeSystemControls,
        nativeSystemControls
      )
    }else if(nativeSystemControls&&pendingCall?.call_id===callId){
      $('incomingActions')?.classList.remove('hidden');
      if(data?.from_avatar_url){
        pendingCall.from_avatar_url=data.from_avatar_url
      }
      setCallVisual(
        data?.from_name||pendingCall?.from_name||$('callName')?.textContent,
        data?.from_avatar_url||pendingCall?.from_avatar_url||null
      )
    }

    if(action.nativeAccept){
      const readyStarted=Date.now();
      while(
        !pendingCall
        && !currentCall
        && Date.now()-readyStarted<5000
      ){
        await new Promise(resolve=>setTimeout(resolve,120))
      }

      if(currentCall?.callId===callId){
        clearPendingNativeCallAction();
        history.replaceState({},'',location.pathname);
        return
      }
      if(!pendingCall||pendingCall.call_id!==callId){
        throw new Error('Входящий звонок ещё не готов')
      }

      // A cold Android WebView can report itself visible slightly before
      // microphone/WebRTC is fully ready. Give the Activity a moment and
      // retry transient failures instead of rejecting the call immediately.
      await new Promise(resolve=>setTimeout(resolve,750));

      let accepted=false;
      let lastError=null;
      for(let attempt=0;attempt<3&&!accepted;attempt++){
        try{
          accepted=await acceptIncomingCall({
            nativeResume:true,
            suppressFailureAlert:true
          })
        }catch(err){
          lastError=err
        }
        if(accepted)break;

        if(currentCall?.callId===callId){
          accepted=true;
          break
        }

        if(!pendingCall||pendingCall.call_id!==callId){
          try{
            const refreshed=await api(
              '/api/calls/pending/'+encodeURIComponent(callId)
            );
            incomingCall(refreshed,true,true)
          }catch(err){
            lastError=err;
            break
          }
        }

        $('callStatus').textContent='Подключаем звонок…';
        await new Promise(resolve=>setTimeout(resolve,650+attempt*350))
      }

      if(!accepted&&currentCall?.callId!==callId){
        throw lastError||new Error('Не удалось подключить звонок')
      }
    }

    clearPendingNativeCallAction();
    history.replaceState({},'',location.pathname)
  }finally{
    if(action.nativeAccept&&nativeAcceptRestoreCallId===callId){
      nativeAcceptRestoreCallId=null
    }
  }
}

window.handleNativeCallAction=path=>{
  try{
    const url=new URL(String(path||''),location.origin);
    const action={
      callId:url.searchParams.get('incoming_call'),
      nativeRing:url.searchParams.get('native_ring')==='1',
      nativeAccept:url.searchParams.get('native_accept')==='1'
    };
    if(!action.callId)return false;
    savePendingNativeCallAction(action);
    // Android evaluateJavascript needs a synchronous acknowledgement.
    // Keep recovery asynchronous without reloading the live call screen.
    resumeIncomingCallAction(action).catch(err=>{
      console.warn('native call action failed',err)
    });
    return true
  }catch(err){
    console.warn('native call action failed',err);
    return false
  }
};

async function resumeIncomingCallFromUrl(){
  const fromUrl=incomingActionFromLocation();
  const saved=readPendingNativeCallAction();
  // A notification answer takes priority over the ringing URL already open.
  const action=saved?.nativeAccept?saved:(fromUrl||saved);
  if(!action)return;
  return resumeIncomingCallAction(action)
}

async function resumeIncomingCallAction(action){
  savePendingNativeCallAction(action);

  try{
    await restoreIncomingCallAction(action)
  }catch(err){
    if(err?.status===404){
      clearNativeCallNotification(action.callId);
      clearPendingNativeCallAction();
      history.replaceState({},'',location.pathname);
      alert('Этот звонок уже завершён')
    }else{
      // Preserve the answer on warm launches too, while the socket/media
      // becomes ready. Never reload an active WebView to retry acceptance.
      setTimeout(()=>{
        const retry=readPendingNativeCallAction();
        if(retry&&me){
          restoreIncomingCallAction(retry).catch(()=>{})
        }
      },1200)
    }
  }
}

function groupReceiptUpdateKey(groupId,messageId){
  return String(Number(groupId)||0)+':'+String(Number(messageId)||0)
}

function updateGroupReceiptSummary(groupId,messageId,summary){
  const key=groupReceiptUpdateKey(groupId,messageId);
  if(
    active?.type!=='group'
    ||Number(active.data.id)!==Number(groupId)
  ){
    return
  }

  const index=currentMessages.findIndex(
    item=>Number(item.id)===Number(messageId)
  );
  if(index<0){
    pendingGroupReceiptSummaries.set(key,{...(summary||{})});
    if(pendingGroupReceiptSummaries.size>100){
      const oldest=pendingGroupReceiptSummaries.keys().next().value;
      if(oldest)pendingGroupReceiptSummaries.delete(oldest)
    }
    return
  }

  pendingGroupReceiptSummaries.delete(key);
  const updated={
    ...currentMessages[index],
    receipt_summary:{...(summary||{})}
  };
  currentMessages[index]=updated;
  const node=document.querySelector(
    '.bubble[data-message-id="'+String(messageId)+'"]'
  );
  if(node)node.replaceWith(msgNode(updated))
}

function applyGroupReceiptUpdates(data){
  const groupId=Number(data?.group_id);
  const updates=Array.isArray(data?.updates)?data.updates:[];
  for(const update of updates){
    updateGroupReceiptSummary(
      groupId,
      Number(update?.message_id),
      update?.summary||{}
    )
  }
}

function ackGroupMessageDelivered(message){
  const groupId=Number(message?.group_id);
  const messageId=Number(message?.id);
  if(
    !groupId
    ||!messageId
    ||Number(message?.sender_id)===Number(me?.id)
  )return;
  api(
    '/api/groups/'+groupId+'/messages/'+messageId+'/delivered',
    {method:'POST'}
  ).catch(()=>{})
}

const wsPingWaiters=new Map();

function resolveWsPing(data){
  const nonce=String(data?.nonce||'');
  const waiter=wsPingWaiters.get(nonce);
  if(!waiter)return false;
  wsPingWaiters.delete(nonce);
  clearTimeout(waiter.timer);
  waiter.resolve(Math.max(0,performance.now()-waiter.started));
  return true
}

function measureWsPing(timeoutMs=3500){
  return new Promise(async(resolve,reject)=>{
    try{await waitForSocketOpen(Math.min(timeoutMs,3000))}catch(err){reject(err);return}
    const nonce='preflight-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
    const timer=setTimeout(()=>{
      wsPingWaiters.delete(nonce);
      reject(new Error('Нет ответа от сервера'))
    },timeoutMs);
    wsPingWaiters.set(nonce,{resolve,reject,timer,started:performance.now()});
    try{wsSend({type:'ws_ping_probe',nonce})}catch(err){
      clearTimeout(timer);wsPingWaiters.delete(nonce);reject(err)
    }
  })
}

let wsHealthPingTimer=null;
let wsHealthPingBusy=false;

async function reportWsHealthPing(){
  if(
    wsHealthPingBusy
    || !me
    || socket?.readyState!==WebSocket.OPEN
  )return;
  const checkedSocket=socket;
  wsHealthPingBusy=true;
  try{
    const ping=await measureWsPing(3000);
    if(socket===checkedSocket&&socket?.readyState===WebSocket.OPEN){
      wsSend({
        type:'ws_ping_report',
        ping_ms:Math.max(1,Math.round(ping))
      })
    }
  }catch{
    if(me&&navigator.onLine!==false&&socket===checkedSocket
      &&checkedSocket.readyState===WebSocket.OPEN){
      connectWs()
    }
  }finally{
    if(socket===checkedSocket)wsHealthPingBusy=false
  }
}

function ensureWsConnection(){
  if(!me||navigator.onLine===false)return;
  if(socket?.readyState===WebSocket.OPEN){
    reportWsHealthPing().catch(()=>{})
  }else if(socket?.readyState!==WebSocket.CONNECTING){
    connectWs()
  }
}

function startWsHealthPingMonitoring(){
  if(wsHealthPingTimer)clearInterval(wsHealthPingTimer);
  setTimeout(()=>reportWsHealthPing().catch(()=>{}),900);
  wsHealthPingTimer=setInterval(()=>{
    reportWsHealthPing().catch(()=>{})
  },30000)
}

function stopWsHealthPingMonitoring(){
  if(wsHealthPingTimer){
    clearInterval(wsHealthPingTimer);
    wsHealthPingTimer=null
  }
  wsHealthPingBusy=false
}

let wsAckTimer=null;
let wsAckPendingSeq=0;
let wsBatchingUi=false;
let wsUsersRenderPending=false;
let wsGroupsRenderPending=false;
const wsUserRowsPending=new Set();
const wsGroupRowsPending=new Set();

function wsSeqStorageKey(){
  return 'svoi_ws_seq_'+String(Number(me?.id)||0)
}

function readWsLastSeq(){
  const value=Number(localStorage.getItem(wsSeqStorageKey())||0);
  return Number.isSafeInteger(value)&&value>0?value:0
}

function scheduleWsAck(seq){
  seq=Number(seq)||0;
  if(!seq)return;
  wsAckPendingSeq=Math.max(wsAckPendingSeq,seq);
  if(wsAckTimer)return;
  wsAckTimer=setTimeout(()=>{
    wsAckTimer=null;
    if(!wsAckPendingSeq)return;
    const ackSeq=wsAckPendingSeq;
    wsAckPendingSeq=0;
    if(socket?.readyState===WebSocket.OPEN){
      try{socket.send(JSON.stringify({type:'ws_ack',seq:ackSeq}))}catch{}
    }
  },90)
}

function shouldProcessWsSequence(data){
  const seq=Number(data?._seq)||0;
  if(!seq)return true;
  const last=readWsLastSeq();
  if(seq<=last){
    scheduleWsAck(seq);
    return false
  }
  return true
}

function commitWsSequence(data){
  const seq=Number(data?._seq)||0;
  if(!seq)return;
  localStorage.setItem(wsSeqStorageKey(),String(seq));
  scheduleWsAck(seq)
}

function wsRenderUsers(userId=null){
  const id=Number(userId)||0;
  if(wsBatchingUi){
    if(id)wsUserRowsPending.add(id);
    else wsUsersRenderPending=true;
    return
  }
  if(id){
    if(!updateUserRow(id))renderUsers();
  }else{
    renderUsers()
  }
}

function wsRenderGroups(groupId=null){
  const id=Number(groupId)||0;
  if(wsBatchingUi){
    if(id)wsGroupRowsPending.add(id);
    else wsGroupsRenderPending=true;
    return
  }
  if(id){
    if(!updateGroupRow(id))renderGroups();
  }else{
    renderGroups()
  }
}

function flushWsUiBatch(){
  const usersPending=wsUsersRenderPending;
  const groupsPending=wsGroupsRenderPending;
  wsUsersRenderPending=false;
  wsGroupsRenderPending=false;

  if(usersPending){
    renderUsers()
  }else{
    for(const userId of wsUserRowsPending){
      if(!updateUserRow(userId)){
        renderUsers();
        break
      }
    }
  }

  if(groupsPending){
    renderGroups()
  }else{
    for(const groupId of wsGroupRowsPending){
      if(!updateGroupRow(groupId)){
        renderGroups();
        break
      }
    }
  }

  wsUserRowsPending.clear();
  wsGroupRowsPending.clear()
}

function connectWs(){
  clearTimeout(retry);
  retry=null;
  if(!me||navigator.onLine===false)return;
  stopWsHealthPingMonitoring();
  const oldSocket=socket;
  socket=null;
  if(oldSocket)oldSocket.close();
  const proto=location.protocol==='https:'?'wss':'ws';
  const lastSeq=readWsLastSeq();
  const params=new URLSearchParams({last_seq:String(lastSeq)});
  const ws=new WebSocket(proto+'://'+location.host+'/ws?'+params.toString());
  socket=ws;
  ws.onopen=()=>{
    if(socket!==ws)return;
    startWsHealthPingMonitoring();
    scheduleOutboxFlush(120);
    const reconnect=wsHasConnected;
    wsHasConnected=true;
    if(reconnect&&me){
      if(wsReconnectSyncTimer)clearTimeout(wsReconnectSyncTimer);
      wsReconnectSyncTimer=setTimeout(()=>{
        wsReconnectSyncTimer=null;
        if(!me||socket!==ws||ws.readyState!==WebSocket.OPEN)return;
        Promise.allSettled([loadUsers(),loadGroups()]).catch(()=>{})
      },350)
    }

    const action=readPendingNativeCallAction();
    if(action){
      restoreIncomingCallAction(action).catch(()=>{})
    }
    if(currentCall?.callId){
      sendPrivateMuteState(true);
      sendPrivateVideoState(true);
      if(currentCall.answered&&!currentCall.recovering&&currentCall.pc
        &&!['connected','closed'].includes(currentCall.pc.connectionState)){
        currentCall.recoveryAttempts=0;
        schedulePrivateCallRecovery(currentCall,120)
      }
    }
    if(groupCallState){
      sendGroupPingProbe(groupCallState)
    }
  };
  ws.onmessage=e=>{
    if(socket!==ws)return;
    let packet=null;
    try{packet=JSON.parse(e.data)}catch{return}
    if(packet?.type==='ws_batch'&&Array.isArray(packet.events)){
      wsBatchingUi=true;
      try{
        for(const event of packet.events)processWsEvent(event)
      }finally{
        wsBatchingUi=false;
        flushWsUiBatch()
      }
      return
    }
    processWsEvent(packet)
  };

  function processWsEvent(data){
    if(!data||!shouldProcessWsSequence(data))return;
    try{
      dispatchWsEvent(data)
    }catch(err){
      console.error('WebSocket event processing failed',err,data?.type);
      return
    }
    commitWsSequence(data)
  }

  function dispatchWsEvent(data){
    if(data.type==='ws_ping_pong'){
      resolveWsPing(data);
      return
    }
    if(data.type==='group_ping_pong'){
      handleGroupPingPong(data);
      return
    }
    if(data.type==='group_call_ping'){
      const state=groupCallState;
      if(state&&Number(data.group_id)===Number(state.groupId)){
        const identity=String(data.identity||('user-'+data.from_user_id));
        const ping=Number(data.ping_ms);
        state.participantPings.set(identity,ping);
        updateGroupParticipantPing(identity,ping)
      }
      return
    }
    if(data.type==='group_force_mute'){
      const state=groupCallState;
      if(state&&Number(data.group_id)===Number(state.groupId)){
        state.muted=true;
        state.room.localParticipant
          .setMicrophoneEnabled(false)
          .then(()=>{
            updateGroupMicBadge(
              state.room.localParticipant,
              true,
              true
            );
            $('groupMuteBtn').classList.add('off');
            $('groupMuteBtn').textContent='🔇';
            setGroupCallStatus('Микрофон выключен администратором');
            setTimeout(()=>{
              if(groupCallState===state)refreshGroupCount()
            },2200)
          })
          .catch(()=>{})
      }
      return
    }
    if(data.type==='group_force_mute_sent'){
      const state=groupCallState;
      if(state&&Number(data.group_id)===Number(state.groupId)){
        setGroupCallStatus('Участник заглушён');
        setTimeout(()=>{
          if(groupCallState===state)refreshGroupCount()
        },1600)
      }
      return
    }

    if(data.type==='conference_invite_status'){
      handleConferenceInviteStatus(data);return;
    }
    if(data.type==='private_call_room_upgrade'){
      if(
        currentCall
        &&currentCall.callId===data.call_id
        &&data.invite_token
      ){
        const video=!!data.video;
        promotePrivateCallToConference(data.invite_token,video)
          .catch(err=>{
            alert(err?.message||'Не удалось перейти в конференцию')
          })
      }
      return
    }
    if(['call_offer','call_answer','call_video_offer','call_video_answer','call_video_state','call_mute','ice_candidate','call_reject','call_end','call_unavailable'].includes(data.type)){
      enqueueCallSignal(data);return
    }
    if(data.type==='group_call_invite'){
      playRingPulse().catch(()=>{});
      vibrateGroupInvite();
      if(!groupCallState&&confirm(data.from_name+' зовёт в групповой '+(data.video?'видеозвонок':'звонок')+' «'+data.group_name+'». Присоединиться?')){
        joinGroupCall(data.group_id,!!data.video,false)
      }
      return
    }
    if(data.type==='profile_updated'){
      if(data.user?.id===me?.id){
        me=data.user;$('meName').textContent=me.display_name;$('meUser').textContent='@'+me.username;setAvatar($('meAvatar'),me)
      }
      if(foundUser?.id===data.user?.id){
        foundUser={...foundUser,...data.user};renderUserSearchResult(foundUser)
      }
      const profileId=Number(data.user?.id);
      const profileIndex=users.findIndex(
        item=>Number(item.id)===profileId
      );
      if(profileIndex>=0){
        users=[
          ...users.slice(0,profileIndex),
          {...users[profileIndex],...data.user},
          ...users.slice(profileIndex+1)
        ];
        wsRenderUsers(profileId)
      }
      if(active?.type==='user'&&Number(active.data.id)===profileId){
        active.data={...active.data,...data.user};
        updateHead()
      }
      return
    }
    if(data.type==='typing'){
      const key=data.chat_type+':'+data.chat_id+':'+data.from_user_id;
      if(data.typing){
        incomingTyping.set(key,{
          chat_type:data.chat_type,
          chat_id:Number(data.chat_id),
          from_user_id:Number(data.from_user_id),
          from_name:data.from_name||'Участник',
          expires:Date.now()+4200
        });
        setTimeout(()=>{
          const item=incomingTyping.get(key);
          if(item&&item.expires<=Date.now()){
            incomingTyping.delete(key);
            refreshTypingStatus()
          }
        },4300)
      }else{
        incomingTyping.delete(key)
      }
      refreshTypingStatus();
      return
    }
    if(data.type==='presence'){
      const userId=Number(data.user_id);
      users=users.map(u=>Number(u.id)===userId?{
        ...u,
        online:!!data.online,
        last_seen_at:data.last_seen_at||u.last_seen_at||null
      }:u);

      if(foundUser&&Number(foundUser.id)===userId){
        foundUser={
          ...foundUser,
          online:!!data.online,
          last_seen_at:data.last_seen_at||foundUser.last_seen_at||null
        };
        renderUserSearchResult(foundUser)
      }

      if(active?.type==='user'&&Number(active.data.id)===userId){
        active.data={
          ...active.data,
          online:!!data.online,
          last_seen_at:data.last_seen_at||active.data.last_seen_at||null
        };
        updateHead()
      }

      if(groupMembersData?.members?.some(member=>Number(member.id)===userId)){
        groupMembersData={
          ...groupMembersData,
          members:groupMembersData.members.map(member=>
            Number(member.id)===userId
              ?{
                  ...member,
                  online:!!data.online,
                  last_seen_at:data.last_seen_at||member.last_seen_at||null
                }
              :member
          )
        };
        if($('groupMembersDialog').open)renderGroupMembers(groupMembersData)
      }

      wsRenderUsers(userId);
      return
    }
    if(data.type==='blocks_updated'){
      loadUsers().then(()=>{
        if(active?.type==='user'){
          const fresh=users.find(user=>Number(user.id)===Number(active.data.id));
          if(fresh){
            active.data={...active.data,...fresh};
            updateHead()
          }
        }
      }).catch(()=>{});
      if($('blacklistDialog').open)renderBlacklist().catch(()=>{});
      return
    }
    if(data.type==='contacts_updated'){
      loadUsers().catch(()=>{});
      return
    }
    if(data.type==='message'){
      const m=data.message;
      const senderId=Number(m.sender_id);
      const recipientId=Number(m.recipient_id);
      const activeUserId=active?.type==='user'
        ?Number(active.data.id)
        :0;

      if(document.visibilityState==='visible'&&senderId!==Number(me?.id)&&!isChatMuted('user',senderId)){
        playMessageSound().catch(()=>{});
        vibrateMessage()
      }

      if(
        active?.type==='user'
        && (senderId===activeUserId||recipientId===activeUserId)
      ){
        appendMessage(m);

        if(senderId===activeUserId&&isActiveChatVisible('user',activeUserId)){
          markPrivateChatRead(activeUserId).catch(()=>{})
        }
      }

      // Счётчик личного чата обновляем локально. Полный /api/users нужен
      // только если это первый диалог с пользователем, которого ещё нет
      // в локальном списке.
      applyPrivateMessageToChatList(m);
      return
    }
    if(data.type==='chat_read_sync'){
      const chatType=String(data.chat_type||'');
      const chatId=Number(data.chat_id);
      const unread=Math.max(0,Number(data.unread_count)||0);

      if(chatType==='user'&&chatId){
        users=users.map(item=>
          Number(item.id)===chatId
            ?{...item,unread_count:unread}
            :item
        );
        if(active?.type==='user'&&Number(active.data.id)===chatId){
          active.data={...active.data,unread_count:unread};
          const ids=new Set((data.message_ids||[]).map(Number));
          if(ids.size){
            currentMessages=currentMessages.map(item=>
              ids.has(Number(item.id))
                ?{...item,read_at:data.read_at||item.read_at}
                :item
            )
          }
        }
        wsRenderUsers(chatId);
        updateAppBadge().catch(()=>{})
      }else if(chatType==='group'&&chatId){
        groups=groups.map(item=>
          Number(item.id)===chatId
            ?{...item,unread_count:unread}
            :item
        );
        if(active?.type==='group'&&Number(active.data.id)===chatId){
          active.data={...active.data,unread_count:unread}
        }
        wsRenderGroups(chatId);
        updateAppBadge().catch(()=>{})
      }
      return
    }
    if(data.type==='message_hidden_for_me'){
      const chatType=String(data.chat_type||'');
      const chatId=Number(data.chat_id);
      const messageId=Number(data.message_id);
      const activeMatches=(
        (chatType==='user'&&active?.type==='user'&&Number(active.data.id)===chatId)
        ||(chatType==='group'&&active?.type==='group'&&Number(active.data.id)===chatId)
      );
      if(activeMatches&&messageId){
        removeGroupMessageNode(messageId)
      }
      return
    }
    if(data.type==='message_edited'){
      const m=data.message;
      if(
        active?.type==='user'
        && (
          Number(m.sender_id)===Number(active.data.id)
          || Number(m.recipient_id)===Number(active.data.id)
        )
      ){
        replaceMessageNode(m)
      }
      return
    }
    if(data.type==='message_deleted_all'){
      if(currentMessages.some(item=>Number(item.id)===Number(data.message_id))){
        removeGroupMessageNode(data.message_id)
      }
      return
    }
    if(data.type==='read_receipt'){
      if(
        active?.type==='user'
        && Number(data.reader_id)===Number(active.data.id)
      ){
        const ids=new Set((data.message_ids||[]).map(Number));
        currentMessages=currentMessages.map(item=>
          ids.has(Number(item.id))
            ?{...item,read_at:data.read_at||item.read_at}
            :item
        );
        for(const id of ids){
          const bubble=document.querySelector('.bubble[data-message-id="'+id+'"]');
          const receipt=bubble?.querySelector('.receipt');
          if(receipt){
            receipt.textContent='✓✓ прочитано';
            receipt.classList.add('read')
          }
        }
      }
      return
    }
    if(data.type==='group_receipt_updates'){
      applyGroupReceiptUpdates(data);
      return
    }
    if(data.type==='group_message'){
      let m=data.message;
      const groupId=Number(m.group_id);
      const mine=Number(m.sender_id)===Number(me?.id);
      if(mine){
        const pendingSummary=pendingGroupReceiptSummaries.get(
          groupReceiptUpdateKey(groupId,m.id)
        );
        if(pendingSummary){
          m={...m,receipt_summary:pendingSummary};
          pendingGroupReceiptSummaries.delete(
            groupReceiptUpdateKey(groupId,m.id)
          )
        }
      }
      const selectedHere=active?.type==='group'&&Number(active.data.id)===groupId;
      const activeHere=isActiveChatVisible('group',groupId);
      const shouldNotifyHere=!m.has_mentions||!!m.mentioned_me;

      if(!mine){
        ackGroupMessageDelivered(m)
      }

      if(document.visibilityState==='visible'&&!mine&&shouldNotifyHere&&!isChatMuted('group',groupId)){
        playMessageSound().catch(()=>{});
        vibrateMessage()
      }

      if(selectedHere)appendMessage(m);
      if(activeHere){
        active.data={...active.data,unread_count:0};
        if(!mine){
          api(
            '/api/groups/'+groupId+'/messages/'+m.id+'/read',
            {method:'POST'}
          ).catch(()=>{})
        }
      }

      const knownGroup=groups.some(item=>Number(item.id)===groupId);
      if(knownGroup){
        groups=groups.map(item=>{
          if(Number(item.id)!==groupId)return item;
          const unread=activeHere
            ?0
            :(mine
              ?Number(item.unread_count||0)
              :Number(item.unread_count||0)+1);
          return {...item,unread_count:unread}
        });
        wsRenderGroups(groupId);
        updateAppBadge().catch(()=>{})
      }else{
        // Fallback for a just-added group that has not reached local state yet.
        loadGroups().catch(()=>{})
      }
      return
    }
    if(data.type==='group_message_edited'){
      const m=data.message;
      if(
        active?.type==='group'
        && Number(active.data.id)===Number(data.group_id)
      ){
        replaceMessageNode(m)
      }
      return
    }
    if(data.type==='group_message_deleted'){
      applyGroupMessageDeleted(
        data.group_id,
        data.message_id,
        data.deleted_at,
        !!data.show_deleted_notice
      );
      return
    }
    if(data.type==='group_message_restored'){
      const m=data.message;
      if(active?.type==='group'&&Number(active.data.id)===Number(data.group_id)){
        if(currentMessages.some(item=>Number(item.id)===Number(m.id))){
          replaceMessageNode(m)
        }else{
          currentMessages.push(m);
          currentMessages.sort((a,b)=>Number(a.id)-Number(b.id));
          renderMessages(currentMessages)
        }
      }
      return
    }
    if(data.type==='group_added'){
      const added=data.group;
      const exists=groups.some(g=>Number(g.id)===Number(added.id));
      if(exists){
        groups=groups.map(g=>Number(g.id)===Number(added.id)?added:g)
      }else{
        groups=[added,...groups]
      }
      if(exists)wsRenderGroups(added.id);
      else wsRenderGroups();
      return
    }
    if(data.type==='group_removed'){
      const removedGroupId=Number(data.group_id);
      if(groupCallState&&Number(groupCallState.groupId)===removedGroupId){
        leaveGroupCall(true)
      }
      if($('chatMenuDialog').open)$('chatMenuDialog').close();
      if($('groupMembersDialog').open)$('groupMembersDialog').close();
      if($('groupRenameDialog').open)$('groupRenameDialog').close();

      groups=groups.filter(g=>Number(g.id)!==removedGroupId);
      if(active?.type==='group'&&Number(active.data.id)===removedGroupId){
        applyChatBackgroundVisual('');
        active=null;
        $('app').classList.remove('chat-open');
        $('chatHead').classList.add('hidden');
        $('composer').classList.add('hidden');
        $('messages').innerHTML='<div class="welcome"><div class="mark">С</div><h2>Группа недоступна</h2><p>Вы были исключены из группы.</p></div>';
      }
      wsRenderGroups();
      alert('Вас исключили из группы «'+(data.group_name||'Группа')+'»');
      return
    }
    if(data.type==='group_added'){
      loadGroups().catch(()=>{});
      return
    }
    if(data.type==='group_roles_updated'){
      groups=groups.map(g=>g.id===data.group_id?{...g,is_admin:!!data.is_admin}:g);
      if(active?.type==='group'&&active.data.id===data.group_id){
        active.data={...active.data,is_admin:!!data.is_admin};
        updateHead()
      }
      if($('groupMembersDialog').open&&active?.type==='group'&&active.data.id===data.group_id){
        loadGroupMembers(data.group_id).catch(()=>{})
      }
      wsRenderGroups(data.group_id);
      return
    }
    if(data.type==='group_updated'){
      const updated=data.group;
      const exists=groups.some(g=>g.id===updated.id);
      groups=exists
        ?groups.map(g=>g.id===updated.id?updated:g)
        :[updated,...groups];
      if(active?.type==='group'&&active.data.id===updated.id){
        active.data=updated;
        updateHead();
        if($('groupMembersDialog').open){
          loadGroupMembers(updated.id).catch(()=>{})
        }
        if($('groupRenameDialog').open){
          $('groupRenameInput').value=updated.name||'';
          $('groupInfoCount').textContent='Участников: '+(updated.member_count||0)
        }
      }
      if(groupCallState&&Number(groupCallState.groupId)===Number(updated.id)){
        $('groupCallName').textContent=updated.name
      }
      wsRenderGroups(updated.id);
      return
    }
    if(data.type==='group_created')loadGroups().catch(()=>{})
  }
  ws.onclose=event=>{
    if(socket!==ws)return;
    stopWsHealthPingMonitoring();
    clearTimeout(retry);
    if(event?.code===4401){
      token='';
      localStorage.removeItem('svoi_token');
      try{closePrewarmedPrivateCall()}catch{}
      location.reload();
      return
    }
    if(event?.code===4403){
      reportClientError(
        'manual',
        'WebSocket отклонён политикой Origin',
        {context:'code=4403'}
      ).catch(()=>{});
      return
    }
    // WebRTC media can stay alive when only the signaling WebSocket drops.
    // Keep the call and reconnect signaling faster instead of hanging up.
    if(me&&navigator.onLine!==false){
      retry=setTimeout(connectWs,(currentCall||pendingCall)?700:2500)
    }
  }
}

$('messageActionReply').onclick=()=>{
  const message=messageActionSource?.message;
  if(!message)return;
  closeMessageActions();
  setReplySource(message)
};
$('messageActionEdit').onclick=()=>{
  const message=messageActionSource?.message;
  if(!message)return;
  closeMessageActions();
  openEditMessage(message)
};
$('messageActionForward').onclick=()=>{
  const message=messageActionSource?.message;
  if(!message)return;
  closeMessageActions();
  openForwardDialog(message)
};
$('messageActionCopy').onclick=()=>copyMessageFromMenu();
$('messageActionSeen').onclick=()=>openSeenBy();
$('messageActionDeleteMe').onclick=()=>deleteMessageForMe();
$('messageActionDeleteAll').onclick=()=>deleteMessageForAllFromMenu();
$('closeMessageActions').onclick=closeMessageActions;
$('messageActionsDialog').addEventListener('click',event=>{
  if(event.target===$('messageActionsDialog'))closeMessageActions()
});
$('messageActionsDialog').addEventListener('close',()=>{messageActionSource=null});

$('closeSeenBy').onclick=()=>$('seenByDialog').close();
$('seenByDialog').addEventListener('click',event=>{
  if(event.target===$('seenByDialog'))$('seenByDialog').close()
});

$('closeEditMessage').onclick=closeEditMessageDialog;
$('cancelEditMessage').onclick=closeEditMessageDialog;
$('saveEditMessage').onclick=()=>saveEditedMessage();
$('editMessageText').addEventListener('keydown',event=>{
  if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){
    event.preventDefault();
    saveEditedMessage()
  }
});
$('editMessageDialog').addEventListener('click',event=>{
  if(event.target===$('editMessageDialog'))closeEditMessageDialog()
});
$('editMessageDialog').addEventListener('close',()=>{
  editSource=null;
  $('editMessageError').textContent=''
});

$('closeForwardDialog').onclick=()=>{
  forwardSource=null;
  $('forwardDialog').close()
};
$('forwardDialog').addEventListener('click',event=>{
  if(event.target===$('forwardDialog')){
    forwardSource=null;
    $('forwardDialog').close()
  }
});

$('searchMessagesBtn').onclick=()=>{
  if(!active)return;
  if($('chatMenuDialog').open)$('chatMenuDialog').close();
  $('messageSearchInput').value='';
  $('messageSearchResults').replaceChildren();
  $('messageSearchCount').textContent='Сообщений загружено: '+currentMessages.length;
  $('messageSearchDialog').showModal();
  setTimeout(()=>$('messageSearchInput').focus(),0)
};
$('messageSearchForm').onsubmit=event=>{
  event.preventDefault();
  performMessageSearch($('messageSearchInput').value)
};
$('messageSearchInput').oninput=()=>performMessageSearch($('messageSearchInput').value);
$('closeMessageSearch').onclick=()=>$('messageSearchDialog').close();
$('messageSearchDialog').addEventListener('click',event=>{
  if(event.target===$('messageSearchDialog'))$('messageSearchDialog').close()
});

function chatGalleryTargetKey(){
  if(!active?.data?.id)return '';
  return active.type+':'+Number(active.data.id)
}

function galleryDate(value){
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return '';
  return date.toLocaleString('ru-RU',{
    day:'2-digit',
    month:'2-digit',
    year:'2-digit',
    hour:'2-digit',
    minute:'2-digit'
  })
}

function renderChatGallery(){
  const box=$('chatGalleryContent');
  const kind=chatGalleryState.kind;
  const items=chatGalleryState.items;
  box.replaceChildren();
  box.className='chat-gallery-content';

  $('chatGalleryTabs').querySelectorAll('[data-gallery-kind]').forEach(button=>{
    button.classList.toggle('active',button.dataset.galleryKind===kind)
  });

  if(!items.length){
    const empty=document.createElement('div');
    empty.className='chat-gallery-empty';
    empty.textContent=chatGalleryState.loading?'Загрузка…':'Здесь пока ничего нет';
    box.append(empty)
  }else if(kind==='photos'||kind==='videos'){
    const grid=document.createElement('div');
    grid.className='chat-gallery-grid';
    for(const item of items){
      const card=document.createElement('div');
      card.className='chat-gallery-media';
      const attachment=item.attachment;
      if(kind==='photos'){
        const link=document.createElement('a');
        link.href=attachment.url;
        link.target='_blank';
        link.rel='noopener';
        const img=document.createElement('img');
        img.src=attachment.thumbnail_url||attachment.url;
        img.alt=attachment.name||'Фото';
        img.loading='lazy';
        img.decoding='async';
        img.onerror=()=>{
          if(img.src!==new URL(attachment.url,location.href).href)img.src=attachment.url
        };
        link.append(img);
        card.append(link)
      }else{
        const video=document.createElement('video');
        video.src=attachment.url;
        video.controls=true;
        video.preload='none';
        video.playsInline=true;
        if(attachment.thumbnail_url)video.poster=attachment.thumbnail_url;
        card.append(video)
      }
      const meta=document.createElement('div');
      meta.className='chat-gallery-meta';
      const who=document.createElement('span');
      who.textContent=item.sender_name||'Участник';
      const when=document.createElement('span');
      when.textContent=galleryDate(item.created_at);
      meta.append(who,when);
      card.append(meta);
      grid.append(card)
    }
    box.append(grid)
  }else{
    const list=document.createElement('div');
    list.className='chat-gallery-list';
    for(const item of items){
      const row=document.createElement('div');
      row.className='chat-gallery-row';

      if(kind==='files'){
        const a=document.createElement('a');
        a.href=item.attachment.url;
        a.target='_blank';
        a.rel='noopener';
        a.textContent='📄 '+(item.attachment.name||'Файл');
        row.append(a);
        const size=document.createElement('small');
        size.textContent=formatSize(item.attachment.size||0);
        row.append(size)
      }else if(kind==='voice'){
        const title=document.createElement('strong');
        title.textContent='🎙 Голосовое сообщение';
        row.append(title);
        const audio=document.createElement('audio');
        audio.controls=true;
        audio.preload='none';
        audio.src=item.attachment.url;
        row.append(audio)
      }else{
        for(const url of item.links||[]){
          const a=document.createElement('a');
          a.href=url;
          a.target='_blank';
          a.rel='noopener noreferrer';
          a.textContent=url;
          row.append(a)
        }
        if(item.body){
          const preview=document.createElement('small');
          preview.textContent=item.body.length>180
            ?item.body.slice(0,177)+'…'
            :item.body;
          row.append(preview)
        }
      }

      const meta=document.createElement('small');
      meta.textContent=(item.sender_name||'Участник')+' · '+galleryDate(item.created_at);
      row.append(meta);
      list.append(row)
    }
    box.append(list)
  }

  $('chatGalleryStatus').textContent=items.length
    ?('Показано: '+items.length)
    :'';
  $('chatGalleryMore').classList.toggle(
    'hidden',
    !chatGalleryState.nextBeforeId||chatGalleryState.loading
  )
}

async function loadChatGallery(reset=false){
  if(!active||chatGalleryState.loading)return;
  const key=chatGalleryTargetKey();
  if(!key)return;

  if(reset){
    chatGalleryState.items=[];
    chatGalleryState.nextBeforeId=null
  }

  const requestId=++chatGalleryState.requestId;
  const kind=chatGalleryState.kind;
  const targetType=active.type;
  const targetId=Number(active.data.id);
  const before=reset?null:chatGalleryState.nextBeforeId;

  chatGalleryState.loading=true;
  chatGalleryState.targetKey=key;
  renderChatGallery();

  try{
    let path='/api/chat-gallery?chat_type='+encodeURIComponent(targetType)
      +'&chat_id='+encodeURIComponent(targetId)
      +'&kind='+encodeURIComponent(kind)
      +'&limit=30';
    if(before)path+='&before_id='+encodeURIComponent(before);

    const data=await api(path);
    if(
      requestId!==chatGalleryState.requestId
      ||chatGalleryTargetKey()!==key
      ||chatGalleryState.kind!==kind
    )return;

    chatGalleryState.items=reset
      ?(data.items||[])
      :[...chatGalleryState.items,...(data.items||[])];
    chatGalleryState.nextBeforeId=data.next_before_id||null
  }catch(err){
    if(requestId===chatGalleryState.requestId){
      $('chatGalleryStatus').textContent=err.message||'Не удалось загрузить галерею'
    }
  }finally{
    if(requestId===chatGalleryState.requestId){
      chatGalleryState.loading=false;
      renderChatGallery()
    }
  }
}

function setChatGalleryKind(kind){
  if(!['photos','videos','files','links','voice'].includes(kind))return;
  if(chatGalleryState.kind===kind&&chatGalleryState.items.length)return;
  chatGalleryState.kind=kind;
  chatGalleryState.items=[];
  chatGalleryState.nextBeforeId=null;
  chatGalleryState.requestId++;
  chatGalleryState.loading=false;
  loadChatGallery(true).catch(()=>{})
}

function openChatGallery(){
  if(!active)return;
  if($('chatMenuDialog').open)$('chatMenuDialog').close();
  const title=active.type==='group'
    ?(active.data.name||'Группа')
    :(active.data.display_name||'Чат');
  $('chatGalleryTitle').textContent='Медиа · '+title;
  chatGalleryState.kind='photos';
  chatGalleryState.items=[];
  chatGalleryState.nextBeforeId=null;
  chatGalleryState.requestId++;
  chatGalleryState.loading=false;
  $('chatGalleryDialog').showModal();
  loadChatGallery(true).catch(()=>{})
}

$('chatGalleryBtn').onclick=openChatGallery;
$('chatGalleryTabs').onclick=event=>{
  const button=event.target.closest('[data-gallery-kind]');
  if(button)setChatGalleryKind(button.dataset.galleryKind)
};
$('chatGalleryMore').onclick=()=>loadChatGallery(false).catch(()=>{});
$('closeChatGallery').onclick=()=>$('chatGalleryDialog').close();
$('chatGalleryDialog').addEventListener('click',event=>{
  if(event.target===$('chatGalleryDialog'))$('chatGalleryDialog').close()
});
$('chatGalleryDialog').addEventListener('close',()=>{
  chatGalleryState.requestId++;
  chatGalleryState.loading=false
});

$('chatBackgroundBtn').onclick=()=>{
  if(!active)return;
  if($('chatMenuDialog').open)$('chatMenuDialog').close();
  $('chatBackgroundError').textContent='';
  applyChatBackgroundVisual(currentChatBackgroundUrl);
  $('chatBackgroundDialog').showModal()
};

$('chooseChatBackground').onclick=()=>$('chatBackgroundInput').click();

$('chatBackgroundInput').onchange=async()=>{
  const file=$('chatBackgroundInput').files?.[0];
  $('chatBackgroundInput').value='';
  const target=currentChatBackgroundTarget();
  if(!file||!target)return;
  if(file.size>20*1024*1024){
    $('chatBackgroundError').textContent='Фото должно быть не больше 20 МБ';
    return
  }
  if(!['image/jpeg','image/png','image/webp'].includes(file.type)){
    $('chatBackgroundError').textContent='Выбери JPEG, PNG или WebP';
    return
  }

  $('chooseChatBackground').disabled=true;
  $('removeChatBackground').disabled=true;
  $('chatBackgroundError').textContent='';
  const form=new FormData();
  form.append('file',file);
  try{
    const response=await fetch(
      '/api/chat-background?chat_type='+encodeURIComponent(target.chat_type)
      +'&chat_id='+target.chat_id,
      {
        method:'POST',
        headers:{...authHeaders()},
        body:form
      }
    );
    let data=null;
    try{data=await response.json()}catch{}
    if(!response.ok)throw new Error(data?.detail||'Не удалось установить фон');

    const now=currentChatBackgroundTarget();
    if(now&&now.chat_type===target.chat_type&&now.chat_id===target.chat_id){
      applyChatBackgroundVisual(data.background_url||'')
    }
  }catch(err){
    $('chatBackgroundError').textContent=err.message||'Не удалось установить фон'
  }finally{
    $('chooseChatBackground').disabled=false;
    $('removeChatBackground').disabled=!currentChatBackgroundUrl
  }
};

$('removeChatBackground').onclick=async()=>{
  const target=currentChatBackgroundTarget();
  if(!target)return;
  $('removeChatBackground').disabled=true;
  $('chatBackgroundError').textContent='';
  try{
    await api(
      '/api/chat-background?chat_type='+encodeURIComponent(target.chat_type)
      +'&chat_id='+target.chat_id,
      {method:'DELETE'}
    );
    const now=currentChatBackgroundTarget();
    if(now&&now.chat_type===target.chat_type&&now.chat_id===target.chat_id){
      applyChatBackgroundVisual('')
    }
  }catch(err){
    $('chatBackgroundError').textContent=err.message||'Не удалось убрать фон'
  }finally{
    $('removeChatBackground').disabled=!currentChatBackgroundUrl
  }
};

$('closeChatBackground').onclick=()=>$('chatBackgroundDialog').close();
$('chatBackgroundDialog').addEventListener('click',event=>{
  if(event.target===$('chatBackgroundDialog'))$('chatBackgroundDialog').close()
});

function openGroupInfoDialog(){
  if(active?.type!=='group')return;
  const canRename=!!active.data.is_admin;
  $('groupRenameInput').value=active.data.name||'';
  $('groupInfoCount').textContent='Участников: '+(active.data.member_count||0);
  $('groupRenameError').textContent='';
  $('groupRenameInput').readOnly=!canRename;
  $('saveGroupRename').classList.toggle('hidden',!canRename);
  $('groupRenameLabel').textContent=canRename
    ?'Название группы — можно изменить'
    :'Название группы';
  $('groupRenameDialog').showModal();
  if(canRename){
    setTimeout(()=>{$('groupRenameInput').focus();$('groupRenameInput').select()},0)
  }
}

function isActiveUserBlocked(){
  return active?.type==='user'&&!!active.data.blocked_by_me
}

function applyActiveBlockState(){
  const blocked=isActiveUserBlocked();
  if(active?.type==='user'){
    $('chatStatus').textContent=blocked?'в чёрном списке':formatLastSeen(active.data);
    $('audioCallBtn').disabled=blocked;
    $('videoCallBtn').disabled=blocked
  }
  syncSend()
}

async function setUserBlocked(userId,blocked){
  const result=await api('/api/blocks/'+userId,{
    method:blocked?'POST':'DELETE'
  });

  users=users.map(user=>
    Number(user.id)===Number(userId)
      ?{...user,blocked_by_me:!!blocked}
      :user
  );
  if(foundUser&&Number(foundUser.id)===Number(userId)){
    foundUser={...foundUser,blocked_by_me:!!blocked};
    renderUserSearchResult(foundUser)
  }
  if(active?.type==='user'&&Number(active.data.id)===Number(userId)){
    active.data={...active.data,blocked_by_me:!!blocked};
    updateHead()
  }
  if(!updateUserRow(userId))renderUsers();
  return result
}

async function renderBlacklist(){
  const box=$('blacklistList');
  box.innerHTML='<div class="muted" style="padding:12px">Загрузка…</div>';
  const items=await api('/api/blocks');
  $('blacklistCount').textContent='Заблокировано: '+items.length;
  box.replaceChildren();

  if(!items.length){
    box.innerHTML='<div class="muted" style="padding:12px">Чёрный список пуст.</div>';
    return
  }

  for(const user of items){
    const row=document.createElement('div');
    row.className='blacklist-entry';

    const avatar=document.createElement('span');
    avatar.className='avatar';
    setAvatar(avatar,user);

    const text=document.createElement('span');
    text.className='txt';
    const name=document.createElement('strong');
    name.textContent=user.display_name;
    const tag=document.createElement('small');
    tag.textContent='@'+user.username;
    text.append(name,tag);

    const unblock=document.createElement('button');
    unblock.type='button';
    unblock.textContent='Разблокировать';
    unblock.onclick=async()=>{
      unblock.disabled=true;
      try{
        await setUserBlocked(user.id,false);
        await renderBlacklist()
      }catch(err){
        alert(err.message||'Не удалось разблокировать')
      }finally{
        unblock.disabled=false
      }
    };

    row.append(avatar,text,unblock);
    box.append(row)
  }
}

function addParticipantRow(user,checked=false,disabled=false){
  const label=document.createElement('label');
  label.className='member-row';

  const input=document.createElement('input');
  input.type='checkbox';
  input.value=String(user.id);
  input.checked=!!checked;
  input.disabled=!!disabled;

  const avatar=document.createElement('span');
  avatar.className='avatar';
  setAvatar(avatar,user);

  const copy=document.createElement('span');
  copy.style.minWidth='0';
  copy.style.flex='1';

  const name=document.createElement('strong');
  name.textContent=user.display_name||user.username;
  name.style.display='block';

  const tag=document.createElement('small');
  tag.className='muted';
  tag.textContent='@'+user.username;
  tag.style.display='block';

  copy.append(name,tag);
  label.append(input,avatar,copy);
  return label
}

async function openAddParticipantDialog(){
  if(!active)return;
  const context={
    type:active.type,
    chatId:Number(active.data.id),
    peer:active.type==='user'?{...active.data}:null
  };
  addParticipantContext=context;

  $('addParticipantError').textContent='';
  $('addParticipantList').replaceChildren();
  $('addParticipantGroupName').classList.toggle('hidden',context.type!=='user');

  if(context.type==='user'){
    $('addParticipantTitle').textContent='Добавить в личный чат';
    $('addParticipantNote').textContent=
      'Будет создана новая группа. Текущая личная переписка останется отдельной.';
    $('addParticipantGroupName').value='Группа с '+(context.peer.display_name||context.peer.username);

    const peerRow=addParticipantRow(context.peer,true,true);
    $('addParticipantList').append(peerRow);

    const candidates=users.filter(user=>
      Number(user.id)!==Number(context.peer.id)
      && Number(user.id)!==Number(me?.id)
    );
    if(!candidates.length){
      const empty=document.createElement('div');
      empty.className='muted';
      empty.style.padding='12px';
      empty.textContent='Других пользователей пока нет.';
      $('addParticipantList').append(empty)
    }else{
      for(const user of candidates){
        $('addParticipantList').append(addParticipantRow(user))
      }
    }
  }else{
    if(!active.data.is_admin)return;
    $('addParticipantTitle').textContent='Добавить в группу';
    $('addParticipantNote').textContent='Выбери одного или нескольких пользователей.';
    $('addParticipantGroupName').value='';

    try{
      const members=await api('/api/groups/'+context.chatId+'/members');
      if(
        !addParticipantContext
        || addParticipantContext.type!=='group'
        || Number(addParticipantContext.chatId)!==context.chatId
      )return;

      const memberIds=new Set((members.members||[]).map(member=>Number(member.id)));
      const candidates=users.filter(user=>
        Number(user.id)!==Number(me?.id)
        && !memberIds.has(Number(user.id))
      );

      if(!candidates.length){
        const empty=document.createElement('div');
        empty.className='muted';
        empty.style.padding='12px';
        empty.textContent='Все доступные пользователи уже в группе.';
        $('addParticipantList').append(empty)
      }else{
        for(const user of candidates){
          $('addParticipantList').append(addParticipantRow(user))
        }
      }
    }catch(err){
      $('addParticipantError').textContent=err.message||'Не удалось загрузить участников'
    }
  }

  $('addParticipantDialog').showModal()
}

async function confirmAddParticipant(){
  const context=addParticipantContext;
  if(!context)return;

  const selected=[...$('addParticipantList').querySelectorAll('input[type="checkbox"]:checked:not(:disabled)')]
    .map(input=>Number(input.value))
    .filter(Boolean);

  if(!selected.length){
    $('addParticipantError').textContent='Выбери хотя бы одного пользователя';
    return
  }

  const button=$('confirmAddParticipant');
  button.disabled=true;
  $('addParticipantError').textContent='';

  try{
    if(context.type==='user'){
      const name=$('addParticipantGroupName').value.trim();
      if(!name){
        $('addParticipantError').textContent='Укажи название новой группы';
        return
      }

      const memberIds=[Number(context.peer.id),...selected];
      const group=await api('/api/groups',{
        method:'POST',
        body:{name,member_ids:memberIds}
      });

      $('addParticipantDialog').close();
      addParticipantContext=null;
      await loadGroups();
      await openGroup(group)
    }else{
      const groupId=context.chatId;
      const selectedUsers=selected
        .map(id=>users.find(user=>Number(user.id)===id))
        .filter(Boolean);

      for(const user of selectedUsers){
        await api('/api/groups/'+groupId+'/members',{
          method:'POST',
          body:{tag:'@'+user.username}
        })
      }

      $('addParticipantDialog').close();
      addParticipantContext=null;
      await loadGroups();
      if(active?.type==='group'&&Number(active.data.id)===groupId){
        const fresh=groups.find(group=>Number(group.id)===groupId);
        if(fresh){
          active.data=fresh;
          updateHead()
        }
      }
    }
  }catch(err){
    $('addParticipantError').textContent=err.message||'Не удалось добавить участника'
  }finally{
    button.disabled=false
  }
}

function openChatMenu(){
  if(!active)return;
  $('chatMenuTitle').textContent=active.type==='group'
    ?active.data.name
    :active.data.display_name;
  $('chatMenuStatus').textContent=active.type==='group'
    ?(active.data.member_count+' участников')
    :formatLastSeen(active.data);
  $('groupInfoBtn').classList.toggle('hidden',active.type!=='group');
  $('groupMembersBtn').classList.toggle('hidden',active.type!=='group');
  $('addParticipantBtn').classList.toggle(
    'hidden',
    active.type==='group'&&!active.data.is_admin
  );
  $('blockUserBtn').classList.toggle('hidden',active.type!=='user');
  $('muteChatBtn').textContent=active.data.muted
    ?'🔕 '+muteStatusText(active.data)
    :'🔔 Уведомления';
  if(active.type==='user'){
    $('blockUserBtn').textContent=active.data.blocked_by_me
      ?'✅ Разблокировать'
      :'🚫 Заблокировать';
    $('blockUserBtn').classList.toggle('danger',!active.data.blocked_by_me)
  }
  $('chatMenuDialog').showModal()
}

function openMuteChatDialog(){
  if(!active)return;
  $('muteChatStatus').textContent=muteStatusText(active.data);
  $('unmuteChatBtn').classList.toggle('hidden',!active.data.muted);
  for(const button of document.querySelectorAll('[data-mute-duration]')){
    button.classList.remove('active')
  }
  $('muteChatDialog').showModal()
}

async function setCurrentChatMute(duration){
  if(!active)return;
  const targetType=active.type==='group'?'group':'user';
  const targetId=Number(active.data.id);
  const buttons=[...document.querySelectorAll('[data-mute-duration]')];
  buttons.forEach(button=>button.disabled=true);
  try{
    const result=await api('/api/chat-mute',{
      method:'POST',
      body:{
        chat_type:targetType,
        chat_id:targetId,
        duration
      }
    });
    const patch={
      muted:!!result.muted,
      muted_until:result.muted_until||null
    };
    if(targetType==='group'){
      groups=groups.map(item=>Number(item.id)===targetId?{...item,...patch}:item);
      if(active?.type==='group'&&Number(active.data.id)===targetId)active.data={...active.data,...patch};
      if(!updateGroupRow(targetId))renderGroups()
    }else{
      users=users.map(item=>Number(item.id)===targetId?{...item,...patch}:item);
      if(active?.type==='user'&&Number(active.data.id)===targetId)active.data={...active.data,...patch};
      if(!updateUserRow(targetId))renderUsers()
    }
    updateHead();
    $('muteChatStatus').textContent=muteStatusText(active.data);
    $('unmuteChatBtn').classList.toggle('hidden',!active.data.muted);
    if($('chatMenuDialog').open)$('chatMenuDialog').close();
    $('muteChatDialog').close()
  }catch(err){
    alert(err.message||'Не удалось изменить уведомления')
  }finally{
    buttons.forEach(button=>button.disabled=false)
  }
}

$('muteChatBtn').onclick=()=>{
  if($('chatMenuDialog').open)$('chatMenuDialog').close();
  openMuteChatDialog()
};
$('closeMuteChat').onclick=()=>$('muteChatDialog').close();
$('muteChatDialog').addEventListener('click',event=>{
  if(event.target===$('muteChatDialog'))$('muteChatDialog').close()
});
for(const button of document.querySelectorAll('[data-mute-duration]')){
  button.onclick=()=>setCurrentChatMute(button.dataset.muteDuration)
}

$('chatName').onclick=()=>openChatMenu();
$('closeChatMenu').onclick=()=>$('chatMenuDialog').close();
$('chatMenuDialog').addEventListener('click',event=>{
  if(event.target===$('chatMenuDialog'))$('chatMenuDialog').close()
});
$('groupInfoBtn').onclick=()=>{
  $('chatMenuDialog').close();
  openGroupInfoDialog()
};
$('addParticipantBtn').onclick=()=>{
  if($('chatMenuDialog').open)$('chatMenuDialog').close();
  openAddParticipantDialog().catch(err=>{
    alert(err?.message||'Не удалось открыть добавление участников')
  })
};
$('cancelAddParticipant').onclick=()=>{
  addParticipantContext=null;
  $('addParticipantDialog').close()
};
$('confirmAddParticipant').onclick=()=>confirmAddParticipant();
$('addParticipantDialog').addEventListener('click',event=>{
  if(event.target===$('addParticipantDialog')){
    addParticipantContext=null;
    $('addParticipantDialog').close()
  }
});
$('addParticipantDialog').addEventListener('close',()=>{
  addParticipantContext=null;
  $('addParticipantError').textContent=''
});
$('blockUserBtn').onclick=async()=>{
  if(active?.type!=='user')return;
  const userId=active.data.id;
  const shouldBlock=!active.data.blocked_by_me;
  if(shouldBlock){
    const ok=confirm('Заблокировать '+active.data.display_name+'? Личные сообщения и звонки будут недоступны.');
    if(!ok)return
  }
  const button=$('blockUserBtn');
  button.disabled=true;
  try{
    await setUserBlocked(userId,shouldBlock);
    $('chatMenuDialog').close()
  }catch(err){
    alert(err.message||'Не удалось изменить чёрный список')
  }finally{
    button.disabled=false
  }
};

$('cancelGroupRename').onclick=()=>$('groupRenameDialog').close();

$('saveGroupRename').onclick=async()=>{
  if(active?.type!=='group'||!active.data.is_admin)return;
  const name=$('groupRenameInput').value.trim();
  if(!name){
    $('groupRenameError').textContent='Название группы не может быть пустым';
    return
  }

  const button=$('saveGroupRename');
  button.disabled=true;
  $('groupRenameError').textContent='';
  try{
    const updated=await api('/api/groups/'+active.data.id,{
      method:'PATCH',
      body:{name}
    });
    groups=groups.map(g=>g.id===updated.id?updated:g);
    if(active?.type==='group'&&active.data.id===updated.id){
      active.data=updated;
      updateHead();
      $('groupInfoCount').textContent='Участников: '+(updated.member_count||0)
    }
    if(!updateGroupRow(updated.id))renderGroups();
    $('groupRenameDialog').close()
  }catch(err){
    $('groupRenameError').textContent=err.message||'Не удалось изменить название'
  }finally{
    button.disabled=false
  }
};

$('groupRenameInput').addEventListener('keydown',event=>{
  if(event.key==='Enter'){
    event.preventDefault();
    $('saveGroupRename').click()
  }
});

$('groupMembersBtn').onclick=()=>{
  if($('chatMenuDialog').open)$('chatMenuDialog').close();
  openGroupMembers()
};
$('closeGroupMembers').onclick=()=>$('groupMembersDialog').close();
$('groupMembersDialog').addEventListener('click',event=>{
  if(event.target===$('groupMembersDialog'))$('groupMembersDialog').close()
});

function openDrawer(){
  $('drawerBackdrop').classList.add('open');
  $('drawerBackdrop').setAttribute('aria-hidden','false');
  document.body.classList.add('drawer-open')
}

function closeDrawer(){
  $('drawerBackdrop').classList.remove('open');
  $('drawerBackdrop').setAttribute('aria-hidden','true');
  document.body.classList.remove('drawer-open')
}

$('menuToggle').onclick=openDrawer;
$('drawerClose').onclick=closeDrawer;
$('drawerBackdrop').addEventListener('click',event=>{
  if(event.target===$('drawerBackdrop'))closeDrawer()
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&$('drawerBackdrop').classList.contains('open'))closeDrawer()
});

$('openRecoverySettings').onclick=()=>{
  closeDrawer();
  $('recoverySetupError').textContent='';
  $('recoveryCurrentPassword').value='';
  $('recoverySetupDialog').showModal()
};

function nativeContactsPlugin(){
  return window.Capacitor?.Plugins?.NativeContacts||null
}

let phoneAccountState=null;

function renderPhoneAccountState(state){
  phoneAccountState=state||{linked:false};
  const box=$('phoneStatus');
  box.replaceChildren();

  const strong=document.createElement('strong');
  const small=document.createElement('small');

  if(phoneAccountState.linked){
    strong.textContent='Номер привязан ·•••• '+(phoneAccountState.last4||'');
    small.textContent='Номер участвует в поиске совпадений телефонной книги.';
    $('phoneLinkBtn').textContent='Изменить номер';
    $('phoneUnlinkBtn').classList.remove('hidden')
  }else{
    strong.textContent='Номер не привязан';
    small.textContent='Укажи номер и текущий пароль, чтобы привязать его.';
    $('phoneLinkBtn').textContent='Привязать номер';
    $('phoneUnlinkBtn').classList.add('hidden')
  }

  box.append(strong,small)
}

async function loadPhoneAccountState(){
  const state=await api('/api/account/phone');
  renderPhoneAccountState(state);
  return state
}

function renderPhoneMatches(matches,localByHash){
  const box=$('phoneMatchList');
  box.replaceChildren();

  if(!matches.length){
    box.innerHTML='<div class="muted" style="padding:10px 2px">Пользователей «Своих» среди контактов пока не найдено.</div>';
    return
  }

  for(const user of matches){
    const local=localByHash.get(user.phone_hash)||null;
    const button=document.createElement('button');
    button.type='button';
    button.className='phone-match';

    const avatar=document.createElement('span');
    avatar.className='avatar';
    setAvatar(avatar,user);

    const txt=document.createElement('span');
    txt.className='txt';

    const name=document.createElement('strong');
    name.textContent=local?.name
      ?local.name+' · '+user.display_name
      :user.display_name;

    const tag=document.createElement('small');
    tag.textContent='@'+user.username+(local?.last4?' · •••• '+local.last4:'');

    txt.append(name,tag);
    button.append(avatar,txt);
    button.onclick=()=>{
      $('phoneContactsDialog').close();
      openUser(user)
    };
    box.append(button)
  }
}

$('openPhoneContacts').onclick=async()=>{
  closeDrawer();
  $('phoneLinkError').textContent='';
  $('phoneSyncStatus').textContent='';
  $('phoneMatchList').replaceChildren();
  $('phoneNumberInput').value='';
  $('phonePasswordInput').value='';
  try{
    await loadPhoneAccountState()
  }catch(err){
    $('phoneLinkError').textContent=err.message||'Не удалось загрузить настройки телефона'
  }

  const plugin=nativeContactsPlugin();
  if(!plugin){
    $('syncPhoneContactsBtn').disabled=true;
    $('phoneSyncStatus').textContent='Синхронизация телефонной книги доступна в Android-приложении «Свои».'
  }else{
    $('syncPhoneContactsBtn').disabled=false
  }

  $('phoneContactsDialog').showModal()
};

$('closePhoneContacts').onclick=()=>$('phoneContactsDialog').close();

$('phoneLinkForm').onsubmit=async event=>{
  event.preventDefault();
  const button=$('phoneLinkBtn');
  $('phoneLinkError').textContent='';

  const phone=$('phoneNumberInput').value.trim();
  const password=$('phonePasswordInput').value;
  if(!phone){
    $('phoneLinkError').textContent='Укажи номер телефона';
    return
  }
  if(!password){
    $('phoneLinkError').textContent='Введи текущий пароль';
    return
  }

  button.disabled=true;
  try{
    const state=await api('/api/account/phone/link',{
      method:'POST',
      body:{phone,current_password:password}
    });
    renderPhoneAccountState(state);
    $('phoneNumberInput').value='';
    $('phonePasswordInput').value='';
    $('phoneSyncStatus').textContent=
      'Номер привязан. Теперь можно синхронизировать телефонную книгу.'
  }catch(err){
    $('phoneLinkError').textContent=err.message||'Не удалось привязать номер'
  }finally{
    button.disabled=false
  }
};

$('phoneUnlinkBtn').onclick=async()=>{
  const password=$('phonePasswordInput').value;
  if(!password){
    $('phoneLinkError').textContent='Для отвязки введи текущий пароль';
    $('phonePasswordInput').focus();
    return
  }
  if(!confirm('Отвязать номер от аккаунта? Другие пользователи больше не смогут находить тебя по телефонной книге.'))return;

  const button=$('phoneUnlinkBtn');
  button.disabled=true;
  $('phoneLinkError').textContent='';
  try{
    const state=await api('/api/account/phone',{
      method:'DELETE',
      body:{current_password:password}
    });
    renderPhoneAccountState(state);
    $('phonePasswordInput').value='';
    $('phoneSyncStatus').textContent='Номер отвязан.'
  }catch(err){
    $('phoneLinkError').textContent=err.message||'Не удалось отвязать номер'
  }finally{
    button.disabled=false
  }
};

$('syncPhoneContactsBtn').onclick=async()=>{
  const plugin=nativeContactsPlugin();
  if(!plugin){
    $('phoneSyncStatus').textContent='Эта функция доступна только в Android-приложении.';
    return
  }

  const button=$('syncPhoneContactsBtn');
  button.disabled=true;
  $('phoneSyncStatus').textContent='Читаем телефонную книгу…';
  $('phoneMatchList').replaceChildren();

  try{
    const nativeResult=await plugin.readHashedContacts();
    const contacts=Array.isArray(nativeResult?.contacts)
      ?nativeResult.contacts
      :[];

    const localByHash=new Map();
    for(const item of contacts){
      if(item?.hash)localByHash.set(item.hash,item)
    }

    $('phoneSyncStatus').textContent='Сопоставляем '+contacts.length+' номеров…';

    const result=await api('/api/phone-contacts/sync',{
      method:'POST',
      body:{hashes:[...localByHash.keys()]}
    });

    await loadUsers();
    renderPhoneMatches(result.users||[],localByHash);
    $('phoneSyncStatus').textContent='Найдено пользователей: '+Number(result.matched||0)
  }catch(err){
    $('phoneSyncStatus').textContent=err.message||'Не удалось синхронизировать контакты'
  }finally{
    button.disabled=false
  }
};

function formatSessionDate(value){
  if(!value)return 'неизвестно';
  try{return new Date(value).toLocaleString('ru-RU')}catch{return String(value)}
}

async function loadAccountSessions(){
  const data=await api('/api/account/sessions');
  const sessions=Array.isArray(data?.sessions)?data.sessions:[];
  $('sessionsCount').textContent='Сессий: '+sessions.length;
  const box=$('sessionsList');box.replaceChildren();
  if(!sessions.length){
    box.innerHTML='<div class="empty">Активных сессий не найдено</div>';
    return
  }
  for(const session of sessions){
    const row=document.createElement('div');row.className='session-row';
    const icon=document.createElement('span');icon.textContent=session.client==='Android'?'📱':session.client==='iPhone/iPad'?'📱':'💻';
    const main=document.createElement('div');main.className='session-main';
    const title=document.createElement('strong');title.textContent=session.device_label||session.client||'Устройство';
    const meta=document.createElement('small');
    meta.textContent='Активность: '+formatSessionDate(session.last_seen_at)+' · вход: '+formatSessionDate(session.created_at);
    main.append(title,meta);
    row.append(icon,main);
    if(session.current){
      const badge=document.createElement('span');badge.className='session-current';badge.textContent='Это устройство';row.append(badge)
    }else{
      const end=document.createElement('button');end.className='session-end';end.type='button';end.textContent='Завершить';
      end.onclick=async()=>{
        end.disabled=true;
        try{
          await api('/api/account/sessions/'+encodeURIComponent(session.id),{method:'DELETE'});
          await loadAccountSessions()
        }catch(err){alert(err.message||'Не удалось завершить сессию');end.disabled=false}
      };
      row.append(end)
    }
    box.append(row)
  }
  $('revokeOtherSessions').disabled=sessions.filter(item=>!item.current).length===0
}

$('openSessions').onclick=async()=>{
  closeDrawer();
  $('sessionsDialog').showModal();
  $('sessionsList').innerHTML='<div class="empty">Загрузка…</div>';
  try{await loadAccountSessions()}catch(err){
    const errorBox=document.createElement('div');
    errorBox.className='error';
    errorBox.style.padding='12px';
    errorBox.textContent=err.message||'Не удалось загрузить сессии';
    $('sessionsList').replaceChildren(errorBox)
  }
};
$('closeSessions').onclick=()=>$('sessionsDialog').close();
$('doneSessions').onclick=()=>$('sessionsDialog').close();
$('sessionsDialog').addEventListener('click',event=>{
  if(event.target===$('sessionsDialog'))$('sessionsDialog').close()
});
$('revokeOtherSessions').onclick=async()=>{
  if(!confirm('Завершить все остальные сессии аккаунта?'))return;
  const button=$('revokeOtherSessions');button.disabled=true;
  try{
    const result=await api('/api/account/sessions/revoke-others',{method:'POST'});
    await loadAccountSessions();
    alert('Завершено сессий: '+Number(result?.revoked||0))
  }catch(err){
    alert(err.message||'Не удалось завершить сессии')
  }finally{button.disabled=false}
};

$('openBlacklist').onclick=async()=>{
  closeDrawer();
  $('blacklistDialog').showModal();
  try{await renderBlacklist()}catch(err){
    const errorBox=document.createElement('div');
    errorBox.className='error';
    errorBox.style.padding='12px';
    errorBox.textContent=err.message||'Не удалось загрузить чёрный список';
    $('blacklistList').replaceChildren(errorBox)
  }
};
$('closeBlacklist').onclick=()=>$('blacklistDialog').close();
$('blacklistDialog').addEventListener('click',event=>{
  if(event.target===$('blacklistDialog'))$('blacklistDialog').close()
});

let callPreflightRunning=false;

function setPreflightRows(rows){
  const box=$('callPreflightList');box.replaceChildren();
  for(const row of rows){
    const item=document.createElement('div');item.className='call-preflight-row';
    const icon=document.createElement('span');icon.className='call-preflight-icon';icon.textContent=row.icon||'•';
    const title=document.createElement('strong');title.textContent=row.title;
    const detail=document.createElement('small');detail.textContent=row.detail||'';
    item.append(icon,title,detail);box.append(item)
  }
}

function gatherIceCandidate(config,relayOnly=false,timeoutMs=4500){
  return new Promise(async(resolve)=>{
    let pc=null;
    let settled=false;
    const finish=result=>{
      if(settled)return;settled=true;
      try{pc?.close()}catch{}
      resolve(result)
    };
    const timer=setTimeout(()=>finish({ok:false,type:null}),timeoutMs);
    try{
      pc=new RTCPeerConnection({
        ...config,
        iceTransportPolicy:relayOnly?'relay':'all',
        iceCandidatePoolSize:0
      });
      pc.createDataChannel('preflight');
      pc.onicecandidate=event=>{
        const candidate=event.candidate;
        if(!candidate){
          clearTimeout(timer);finish({ok:false,type:null});return
        }
        const text=String(candidate.candidate||'');
        const match=text.match(/\btyp\s+(\w+)/);
        const type=match?.[1]||candidate.type||'candidate';
        if(!relayOnly||type==='relay'){
          clearTimeout(timer);finish({ok:true,type})
        }
      };
      const offer=await pc.createOffer();
      await pc.setLocalDescription(offer)
    }catch(err){
      clearTimeout(timer);finish({ok:false,type:null,error:err})
    }
  })
}

async function runCallPreflight(){
  if(callPreflightRunning)return;
  callPreflightRunning=true;
  $('startCallPreflight').disabled=true;
  $('callPreflightSummary').textContent='Проверяем соединение…';
  const rows=[];
  const update=(title,icon,detail)=>{
    const existing=rows.find(item=>item.title===title);
    if(existing){existing.icon=icon;existing.detail=detail}else rows.push({title,icon,detail});
    setPreflightRows(rows)
  };

  const connection=navigator.connection||navigator.mozConnection||navigator.webkitConnection;
  update('Сеть',navigator.onLine?'✅':'❌',navigator.onLine
    ?[connection?.effectiveType,connection?.downlink?connection.downlink+' Мбит/с':null,connection?.rtt?connection.rtt+' мс':null].filter(Boolean).join(' · ')||'Подключение есть'
    :'Устройство офлайн');

  try{
    const ping=await measureWsPing();
    update('Сервер / WebSocket','✅',Math.round(ping)+' мс')
  }catch(err){
    update('Сервер / WebSocket','❌',err.message||'Нет ответа')
  }

  let mediaStream=null;
  try{
    mediaStream=await navigator.mediaDevices.getUserMedia({
      audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},
      video:{width:{ideal:640},height:{ideal:360},frameRate:{ideal:20,max:24}}
    });
    const audio=mediaStream.getAudioTracks()[0];
    const video=mediaStream.getVideoTracks()[0];
    update('Микрофон',audio?'✅':'⚠️',audio?.label||'Не обнаружен');
    update('Камера',video?'✅':'⚠️',video?.label||'Не обнаружена')
  }catch(err){
    update('Микрофон / камера','❌',err.name==='NotAllowedError'?'Нет разрешения':(err.message||'Ошибка доступа'))
  }finally{
    try{mediaStream?.getTracks().forEach(track=>track.stop())}catch{}
  }

  let rtcConfig=null;
  try{
    rtcConfig=await getRtcConfig();
    const ice=await gatherIceCandidate(rtcConfig,false,4000);
    update('WebRTC / ICE',ice.ok?'✅':'❌',ice.ok?'Кандидат: '+ice.type:'Кандидаты не получены');
    const turn=await gatherIceCandidate(rtcConfig,true,5000);
    update('TURN fallback',turn.ok?'✅':'⚠️',turn.ok?'Relay доступен':'Relay-кандидат не получен')
  }catch(err){
    update('WebRTC / ICE','❌',err.message||'Ошибка WebRTC')
  }

  const failed=rows.filter(row=>row.icon==='❌').length;
  const warnings=rows.filter(row=>row.icon==='⚠️').length;
  $('callPreflightSummary').textContent=failed
    ?'Есть проблемы: '+failed+'. Перед звонком лучше их устранить.'
    :warnings
      ?'Связь работает, но есть предупреждения: '+warnings+'.'
      :'Всё готово к звонку.';
  $('startCallPreflight').disabled=false;
  callPreflightRunning=false
}

function openCallPreflight(){
  $('callPreflightSummary').textContent='Нажми «Запустить тест».';
  setPreflightRows([]);
  $('callPreflightDialog').showModal()
}

$('runCallPreflight').onclick=()=>{
  $('callsDialog').close();
  openCallPreflight();
  runCallPreflight().catch(()=>{})
};
$('startCallPreflight').onclick=()=>runCallPreflight().catch(()=>{});
$('closeCallPreflight').onclick=()=>$('callPreflightDialog').close();
$('doneCallPreflight').onclick=()=>$('callPreflightDialog').close();
$('callPreflightDialog').addEventListener('click',event=>{
  if(event.target===$('callPreflightDialog'))$('callPreflightDialog').close()
});

$('openCalls').onclick=async()=>{
  closeDrawer();
  try{await loadCallHistory()}catch{}
  $('callsDialog').showModal()
};
$('closeCalls').onclick=()=>$('callsDialog').close();
$('callsDialog').addEventListener('click',event=>{
  if(event.target===$('callsDialog'))$('callsDialog').close()
});

$('openAdmin').onclick=async()=>{
  closeDrawer();
  if(!me?.is_server_admin)return;
  $('adminDialog').showModal();
  await loadAdminPanel();
  startAdminRefresh()
};
$('refreshAdmin').onclick=()=>loadAdminPanel();
$('adminStatsDay').onclick=()=>setAdminStatsPeriod('day');
$('adminStatsWeek').onclick=()=>setAdminStatsPeriod('week');
$('closeAdmin').onclick=()=>$('adminDialog').close();
$('adminDialog').addEventListener('click',event=>{
  if(event.target===$('adminDialog'))$('adminDialog').close()
});
$('adminDialog').addEventListener('close',stopAdminRefresh);

$('newGroup').onclick=()=>{
  $('groupName').value='';$('groupError').textContent='';const box=$('memberList');box.replaceChildren();
  if(!users.length)box.innerHTML='<div class="muted">Сначала нужен хотя бы один другой пользователь.</div>';
  for(const u of users){
    const row=document.createElement('label');row.className='member-row';
    row.innerHTML='<input type="checkbox" value="'+u.id+'"><span><strong></strong><br><small class="muted">@'+u.username+'</small></span>';
    row.querySelector('strong').textContent=u.display_name;box.append(row)
  }
  $('groupDialog').showModal()
};

$('cancelGroup').onclick=()=>$('groupDialog').close();

$('createGroup').onclick=async()=>{
  const name=$('groupName').value.trim();const member_ids=[...$('memberList').querySelectorAll('input:checked')].map(x=>Number(x.value));
  if(!name){$('groupError').textContent='Укажи название группы';return}
  try{
    const g=await api('/api/groups',{method:'POST',body:{name,member_ids}});
    $('groupDialog').close();await loadGroups();await openGroup(g)
  }catch(err){$('groupError').textContent=err.message}
};

$('meAvatar').onclick=()=>{$('avatarInput').click()};
$('avatarInput').onchange=async()=>{
  const file=$('avatarInput').files?.[0];
  $('avatarInput').value='';
  if(!file)return;
  if(file.size>5*1024*1024){alert('Аватар должен быть не больше 5 МБ');return}
  if(!['image/jpeg','image/png','image/webp'].includes(file.type)){alert('Выбери JPEG, PNG или WebP');return}
  const form=new FormData();form.append('file',file);
  $('meAvatar').disabled=true;
  try{
    const r=await fetch('/api/me/avatar',{
      method:'POST',
      headers:{...authHeaders()},
      body:form
    });
    let data=null;try{data=await r.json()}catch{}
    if(!r.ok)throw new Error(data?.detail||'Не удалось загрузить аватар');
    me=data;setAvatar($('meAvatar'),me);
    await loadUsers()
  }catch(err){alert(err.message||'Не удалось загрузить аватар')}
  finally{$('meAvatar').disabled=false}
};

$('logout').onclick=async()=>{closeDrawer();if(messageRecorder||messageRecordBlob)cancelMessageRecording();if(groupCallState)leaveGroupCall(true);if(currentCall||pendingCall)finishCall(true);await removePushSubscription();try{await api('/api/logout',{method:'POST'})}catch{}token='';localStorage.removeItem('svoi_token');showAuth()};
$('back').onclick=()=>{stopOwnTyping();clearReplySource();$('app').classList.remove('chat-open')};


function initializeCallToolbarLayout(){
  for(const [overlayId,controlsId] of [
    ['callOverlay','activeCallControls'],
    ['groupCallOverlay','groupCallControls']
  ]){
    const overlay=$(overlayId),controls=$(controlsId);
    if(!overlay||!controls)continue;
    const update=()=>{
      const height=Math.ceil(controls.getBoundingClientRect().height);
      if(height>0)overlay.style.setProperty('--call-toolbar-height',height+'px');
    };
    if(typeof ResizeObserver==='function'){
      const observer=new ResizeObserver(update);
      observer.observe(controls);
    }else{
      const observer=new MutationObserver(update);
      observer.observe(overlay,{attributes:true,subtree:true,attributeFilter:['class']});
      window.addEventListener('resize',update);
    }
    update();
  }
}
initializeCallToolbarLayout();

$('auth').classList.add('hidden');
$('app').classList.add('hidden');
showStartupSplash('Подключаемся…');
enter()
