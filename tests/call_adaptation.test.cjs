'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
function extract(start, end) {
  const from = app.indexOf(start);
  const to = app.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, 'source section exists: ' + start);
  return app.slice(from, to);
}

test('all active speakers remain eligible for highlighting', () => {
  assert.match(app, /state\.pendingSpeakers=Array\.isArray\(speakers\)\?speakers\.slice\(\):\[\]/);
  assert.match(app, /activeSpeakerIds\?\.has\(String\(identity\)\)/);
});

test('focus requires a stable speaker and observes a switch cooldown', () => {
  const block = extract('function scheduleGroupSpeakerFocus(identity){', '\nfunction focusGroupTile(tile){');
  const timers = new Map();
  let nextTimer = 1;
  let now = 10000;
  const shown = [];
  const state = {
    video: true, pinnedIdentity: '', groupGridRequested: false,
    groupFocusSource: '', autoSpeakerIdentity: '', speakerFocusTimer: null,
    lastAutoSpeakerFocusAt: 0,
    pendingSpeakers: [{identity: 'user-2'}],
    room: {localParticipant: {identity: 'user-1'}, remoteParticipants: new Map([['user-2', {}], ['user-3', {}]])}
  };
  const tiles = {'tile-user-2': {isConnected: true}, 'tile-user-3': {isConnected: true}};
  const context = {
    groupCallState: state, Date: {now: () => now},
    $: id => id === 'groupCallGrid' ? {querySelector: () => null} : tiles[id],
    groupTileId: identity => 'tile-' + identity,
    showGroupFocusedTile: tile => shown.push(tile),
    setTimeout: (fn, delay) => {const id = nextTimer++; timers.set(id, {fn, delay}); return id;},
    clearTimeout: id => timers.delete(id)
  };
  vm.createContext(context);
  vm.runInContext(block, context);
  context.scheduleGroupSpeakerFocus('user-2');
  assert.equal(timers.size, 1);
  let pending = timers.get(state.speakerFocusTimer);
  assert.equal(pending.delay, 900);
  timers.delete(state.speakerFocusTimer);
  pending.fn();
  assert.equal(shown.length, 1);
  now = 10500;
  state.pendingSpeakers = [{identity: 'user-3'}];
  context.scheduleGroupSpeakerFocus('user-3');
  pending = timers.get(state.speakerFocusTimer);
  assert.equal(pending.delay, 1800);
  state.pendingSpeakers = [{identity: 'user-2'}];
  pending.fn();
  assert.equal(shown.length, 1, 'stale speaker cannot steal the focus');
  state.pinnedIdentity = 'user-2';
  context.scheduleGroupSpeakerFocus('user-3');
  assert.equal(shown.length, 1, 'manual pin remains respected');
});

test('outbound video quality adapts without affecting screen-share tracks', () => {
  const block = extract('function syncGroupOutgoingVideoQuality(', '\nfunction applyGroupVideoNetworkCap(');
  const selected = [];
  const camera = {setPublishingQuality: level => selected.push(level)};
  const screen = {setPublishingQuality: () => assert.fail('screen share must not change')};
  const state = {
    videoNetworkCap: 'high', manualLeave: false,
    room: {localParticipant: {videoTrackPublications: new Map([
      ['camera', {source: 'camera', track: camera}],
      ['screen', {source: 'screen_share', track: screen}]
    ])}}
  };
  const context = {groupCallState: state,
    LivekitClient: {VideoQuality: {LOW: 0, MEDIUM: 1, HIGH: 2}, Track: {Source: {Camera: 'camera'}}},
    WeakMap, console};
  vm.createContext(context);
  vm.runInContext(block, context);
  context.syncGroupOutgoingVideoQuality();
  context.syncGroupOutgoingVideoQuality();
  state.videoNetworkCap = 'low';
  context.syncGroupOutgoingVideoQuality();
  state.videoNetworkCap = 'medium';
  context.syncGroupOutgoingVideoQuality();
  assert.deepEqual(selected, [2, 0, 1]);
});

test('duplicate conference migration starts only one handoff', async () => {
  const block = extract('let privateConferenceMigrationPromise=null;', '\nfunction finishCall(');
  const original = {answered: true, callId: 'call-1', video: false};
  const create = new Function('call', 'join', 'let currentCall=call, joinGroupCall=join;\n' + block +
    '\nreturn {promotePrivateCallToConference,getCall:()=>currentCall};');
  let resolveJoin;
  const deferred = new Promise(resolve => {resolveJoin = resolve;});
  let attempts = 0;
  const one = create(original, (...args) => {
    attempts++;
    assert.equal(args[5].migrationCall, original);
    return deferred;
  });
  const a = one.promotePrivateCallToConference('test-token');
  const b = one.promotePrivateCallToConference('test-token');
  assert.equal(attempts, 1);
  resolveJoin(true);
  assert.equal((await a).callId, original.callId);
  assert.equal((await b).callId, original.callId);
  const failed = create(original, async () => {throw Error('LiveKit disconnected');});
  await assert.rejects(failed.promotePrivateCallToConference('test-token'), /LiveKit disconnected/);
  assert.equal(failed.getCall(), original);
});

test('conference handoff connects before disconnecting original peer', () => {
  const block = extract('async function joinGroupCall(', '\nfunction leaveGroupCall(');
  const connect = block.indexOf('room.connect(credentials.server_url');
  const activate = block.indexOf('await options.onConferenceReady()');
  const closeOriginal = block.indexOf('closePrivateCallForConference()');
  assert.ok(connect >= 0 && connect < activate && activate < closeOriginal);
  assert.match(block, /room\.state!=='connected'/);
  assert.match(app, /const keepPrivateCall=!!currentCall\?\.answered&&!!groupCallState/);
});

test('PWA shell uses one new cache generation', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const sw = fs.readFileSync(path.join(__dirname, '..', 'sw.js'), 'utf8');
  assert.match(sw, /svoi-shell-v119/);
  assert.match(html, /app\.js\?v=119/);
  assert.match(sw, /app\.js\?v=119/);
  assert.doesNotMatch(html, /v=118/);
});

test('iOS group call can request audio playback from a direct user tap', () => {
  assert.match(app, /RoomEvent\.AudioPlaybackStatusChanged/);
  assert.match(app, /room\.startAudio\(\)/);
  assert.match(app, /room\.canPlaybackAudio/);
});
