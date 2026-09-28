from pathlib import Path

manifest = Path("android/app/src/main/AndroidManifest.xml")
text = manifest.read_text()

permissions = """    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PROJECTION" />
"""

if "android.permission.CAMERA" not in text:
    start = text.find(">", text.find("<manifest"))
    if start == -1:
        raise SystemExit("AndroidManifest.xml: manifest tag not found")
    text = text[: start + 1] + "\n" + permissions + text[start + 1 :]
else:
    start = text.find(">", text.find("<manifest"))
    extra_permissions = []
    for permission in [
        "android.permission.REQUEST_INSTALL_PACKAGES",
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.FOREGROUND_SERVICE_MEDIA_PROJECTION",
    ]:
        if permission not in text:
            extra_permissions.append(
                f'    <uses-permission android:name="{permission}" />'
            )
    if extra_permissions:
        text = (
            text[: start + 1]
            + "\n"
            + "\n".join(extra_permissions)
            + "\n"
            + text[start + 1 :]
        )

service_decl = """        <service
            android:name=".ScreenShareService"
            android:exported="false"
            android:foregroundServiceType="mediaProjection" />
"""
if 'android:name=".ScreenShareService"' not in text:
    app_end = text.find("</application>")
    if app_end == -1:
        raise SystemExit("AndroidManifest.xml: application tag not found")
    text = text[:app_end] + service_decl + text[app_end:]

manifest.write_text(text)

gradle = Path("android/app/build.gradle")
gradle_text = gradle.read_text()
webrtc_dependency = "implementation 'io.github.webrtc-sdk:android:150.7871.01'"
if webrtc_dependency not in gradle_text:
    marker = "dependencies {"
    pos = gradle_text.find(marker)
    if pos == -1:
        raise SystemExit("android/app/build.gradle: dependencies block not found")
    pos += len(marker)
    gradle_text = (
        gradle_text[:pos]
        + "\n    "
        + webrtc_dependency
        + gradle_text[pos:]
    )
gradle.write_text(gradle_text)

main_activity = Path(
    "android/app/src/main/java/ru/svoi/mobile/MainActivity.java"
)

main_activity.write_text(r'''package ru.svoi.mobile;

import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.ComponentName;
import android.content.pm.ApplicationInfo;
import android.content.pm.ResolveInfo;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.widget.Toast;

import com.getcapacitor.BridgeActivity;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.List;

public class MainActivity extends BridgeActivity {
    private static final String LATEST_RELEASE =
        "https://api.github.com/repos/geodenix/svoi-chat/releases/latest";
    private static final String PREFS = "svoi_updater";
    private static final String PREF_DOWNLOAD_ID = "download_id";
    private static final String PREF_PENDING_INSTALL = "pending_install";

    private SharedPreferences updaterPrefs;
    private BroadcastReceiver downloadReceiver;
    private volatile boolean updateCheckRunning = false;
    private boolean updateDialogShowing = false;
    private long lastUpdateCheckAt = 0L;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(NativeScreenSharePlugin.class);
        registerPlugin(NativeProximityPlugin.class);
        super.onCreate(savedInstanceState);

        updaterPrefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        registerDownloadReceiver();
        resumePendingUpdate();
        checkForUpdates();
    }

    @Override
    public void onResume() {
        super.onResume();
        if (updaterPrefs != null
                && updaterPrefs.getBoolean(PREF_PENDING_INSTALL, false)
                && canInstallPackages()) {
            installDownloadedUpdate();
        }
        checkForUpdates();
    }

    @Override
    public void onDestroy() {
        if (downloadReceiver != null) {
            try {
                unregisterReceiver(downloadReceiver);
            } catch (Exception ignored) {
            }
        }
        super.onDestroy();
    }

    private int currentVersionCode() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                return (int) getPackageManager()
                    .getPackageInfo(getPackageName(), 0)
                    .getLongVersionCode();
            }
            return getPackageManager()
                .getPackageInfo(getPackageName(), 0)
                .versionCode;
        } catch (Exception ignored) {
            return 1;
        }
    }

    private void registerDownloadReceiver() {
        downloadReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                if (!DownloadManager.ACTION_DOWNLOAD_COMPLETE.equals(
                        intent.getAction())) {
                    return;
                }

                long id = intent.getLongExtra(
                    DownloadManager.EXTRA_DOWNLOAD_ID,
                    -1
                );
                long expected = updaterPrefs.getLong(PREF_DOWNLOAD_ID, -1);
                if (id != expected || id < 0) {
                    return;
                }

                installDownloadedUpdate();
            }
        };

        IntentFilter filter = new IntentFilter(
            DownloadManager.ACTION_DOWNLOAD_COMPLETE
        );

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(
                downloadReceiver,
                filter,
                Context.RECEIVER_NOT_EXPORTED
            );
        } else {
            registerReceiver(downloadReceiver, filter);
        }
    }

    private boolean canInstallPackages() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.O
            || getPackageManager().canRequestPackageInstalls();
    }

    private void requestInstallPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }

        try {
            Intent intent = new Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + getPackageName())
            );
            startActivity(intent);
        } catch (Exception ignored) {
            Intent intent = new Intent(
                Settings.ACTION_SECURITY_SETTINGS
            );
            startActivity(intent);
        }
    }

    private void checkForUpdates() {
        long now = System.currentTimeMillis();
        if (updateCheckRunning || now - lastUpdateCheckAt < 10000L) {
            return;
        }
        updateCheckRunning = true;
        lastUpdateCheckAt = now;

        new Thread(() -> {
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection)
                    new URL(LATEST_RELEASE).openConnection();
                connection.setConnectTimeout(7000);
                connection.setReadTimeout(7000);
                connection.setRequestProperty(
                    "Accept",
                    "application/vnd.github+json"
                );
                connection.setRequestProperty(
                    "User-Agent",
                    "Svoi-Android"
                );

                if (connection.getResponseCode() != 200) {
                    return;
                }

                BufferedReader reader = new BufferedReader(
                    new InputStreamReader(connection.getInputStream())
                );
                StringBuilder json = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) {
                    json.append(line);
                }
                reader.close();

                JSONObject release = new JSONObject(json.toString());
                String tag = release.optString("tag_name", "");
                if (!tag.startsWith("android-v")) {
                    return;
                }

                int latestCode;
                try {
                    latestCode = Integer.parseInt(
                        tag.substring("android-v".length())
                    );
                } catch (Exception ignored) {
                    return;
                }

                if (latestCode <= currentVersionCode()) {
                    return;
                }

                String versionName = release.optString("name", tag);
                String downloadUrl = null;
                JSONArray assets = release.optJSONArray("assets");
                if (assets != null) {
                    for (int i = 0; i < assets.length(); i++) {
                        JSONObject asset = assets.getJSONObject(i);
                        if ("svoi.apk".equals(asset.optString("name"))) {
                            downloadUrl = asset.optString(
                                "browser_download_url",
                                null
                            );
                            break;
                        }
                    }
                }

                if (downloadUrl == null || downloadUrl.isEmpty()) {
                    return;
                }

                final String url = downloadUrl;
                final String label = versionName;
                final int code = latestCode;

                runOnUiThread(() -> showUpdateDialog(
                    url,
                    label,
                    code
                ));
            } catch (Exception ignored) {
                // Update checks must never block app startup.
            } finally {
                updateCheckRunning = false;
                if (connection != null) {
                    connection.disconnect();
                }
            }
        }).start();
    }

    private void showUpdateDialog(
        String url,
        String versionName,
        int versionCode
    ) {
        if (isFinishing() || isDestroyed() || updateDialogShowing) {
            return;
        }

        updateDialogShowing = true;
        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("Доступно обновление")
            .setMessage(
                "Новая версия «Свои»: " + versionName +
                "\n\nНажми «Скачать». После загрузки Android " +
                "предложит установить обновление."
            )
            .setNegativeButton("Позже", null)
            .setPositiveButton(
                "Скачать",
                (dialogInterface, which) -> startUpdateDownload(
                    url,
                    versionName,
                    versionCode
                )
            )
            .create();

        dialog.setOnDismissListener(ignored -> {
            updateDialogShowing = false;
        });
        dialog.show();
    }

    private void startUpdateDownload(
        String url,
        String versionName,
        int versionCode
    ) {
        try {
            DownloadManager manager =
                (DownloadManager) getSystemService(DOWNLOAD_SERVICE);

            String fileName = "svoi-update-" + versionCode + ".apk";
            DownloadManager.Request request =
                new DownloadManager.Request(Uri.parse(url));

            request.setTitle("Свои " + versionName);
            request.setDescription("Скачивание обновления");
            request.setMimeType(
                "application/vnd.android.package-archive"
            );
            request.setNotificationVisibility(
                DownloadManager.Request
                    .VISIBILITY_VISIBLE_NOTIFY_COMPLETED
            );
            request.setAllowedOverMetered(true);
            request.setAllowedOverRoaming(true);
            request.setDestinationInExternalFilesDir(
                this,
                Environment.DIRECTORY_DOWNLOADS,
                fileName
            );

            long id = manager.enqueue(request);
            updaterPrefs.edit()
                .putLong(PREF_DOWNLOAD_ID, id)
                .putBoolean(PREF_PENDING_INSTALL, false)
                .apply();

            Toast.makeText(
                this,
                "Обновление скачивается…",
                Toast.LENGTH_LONG
            ).show();
        } catch (Exception error) {
            Toast.makeText(
                this,
                "Не удалось начать загрузку обновления",
                Toast.LENGTH_LONG
            ).show();
        }
    }

    private void resumePendingUpdate() {
        long id = updaterPrefs.getLong(PREF_DOWNLOAD_ID, -1);
        if (id < 0) {
            return;
        }

        DownloadManager manager =
            (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
        DownloadManager.Query query =
            new DownloadManager.Query().setFilterById(id);

        try (Cursor cursor = manager.query(query)) {
            if (cursor == null || !cursor.moveToFirst()) {
                return;
            }

            int statusIndex = cursor.getColumnIndex(
                DownloadManager.COLUMN_STATUS
            );
            if (statusIndex < 0) {
                return;
            }

            int status = cursor.getInt(statusIndex);
            if (status == DownloadManager.STATUS_SUCCESSFUL) {
                installDownloadedUpdate();
            } else if (status == DownloadManager.STATUS_FAILED) {
                updaterPrefs.edit()
                    .remove(PREF_DOWNLOAD_ID)
                    .remove(PREF_PENDING_INSTALL)
                    .apply();
            }
        } catch (Exception ignored) {
        }
    }

    private void installDownloadedUpdate() {
        long id = updaterPrefs.getLong(PREF_DOWNLOAD_ID, -1);
        if (id < 0) {
            return;
        }

        DownloadManager manager =
            (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
        Uri apkUri = manager.getUriForDownloadedFile(id);
        if (apkUri == null) {
            return;
        }

        if (!canInstallPackages()) {
            updaterPrefs.edit()
                .putBoolean(PREF_PENDING_INSTALL, true)
                .apply();

            new AlertDialog.Builder(this)
                .setTitle("Разреши установку обновлений")
                .setMessage(
                    "Android должен разрешить приложению «Свои» " +
                    "устанавливать скачанные обновления. Включи " +
                    "«Разрешить из этого источника», затем вернись " +
                    "в приложение."
                )
                .setNegativeButton("Позже", null)
                .setPositiveButton(
                    "Открыть настройки",
                    (dialog, which) -> requestInstallPermission()
                )
                .show();
            return;
        }

        try {
            // Clear the completed download before opening Android's
            // installer. Otherwise every app restart sees the same
            // successful DownloadManager item and opens the installer again.
            updaterPrefs.edit()
                .remove(PREF_DOWNLOAD_ID)
                .remove(PREF_PENDING_INSTALL)
                .apply();

            Intent install = new Intent(Intent.ACTION_VIEW);
            install.setDataAndType(
                apkUri,
                "application/vnd.android.package-archive"
            );
            install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            install.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);

            // Pick a system package installer explicitly so Android does not
            // show an "Open with" chooser with unrelated apps such as Termux.
            try {
                List<ResolveInfo> handlers =
                    getPackageManager().queryIntentActivities(
                        install,
                        0
                    );

                ResolveInfo best = null;
                for (ResolveInfo handler : handlers) {
                    if (handler.activityInfo == null
                            || handler.activityInfo.applicationInfo == null) {
                        continue;
                    }

                    ApplicationInfo appInfo =
                        handler.activityInfo.applicationInfo;
                    int flags = appInfo.flags;
                    boolean systemApp =
                        (flags & ApplicationInfo.FLAG_SYSTEM) != 0
                        || (flags & ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) != 0;

                    if (!systemApp) {
                        continue;
                    }

                    String packageName =
                        handler.activityInfo.packageName == null
                            ? ""
                            : handler.activityInfo.packageName.toLowerCase();

                    if (packageName.contains("packageinstaller")
                            || packageName.contains("permissioncontroller")) {
                        best = handler;
                        break;
                    }

                    if (best == null) {
                        best = handler;
                    }
                }

                if (best != null) {
                    install.setComponent(
                        new ComponentName(
                            best.activityInfo.packageName,
                            best.activityInfo.name
                        )
                    );
                }
            } catch (Exception ignored) {
            }

            startActivity(install);
        } catch (Exception error) {
            // Restore the id so the user can retry if the system installer
            // could not be opened for some reason.
            updaterPrefs.edit()
                .putLong(PREF_DOWNLOAD_ID, id)
                .putBoolean(PREF_PENDING_INSTALL, false)
                .apply();

            Toast.makeText(
                this,
                "Не удалось открыть установщик обновления",
                Toast.LENGTH_LONG
            ).show();
        }
    }
}
''')



proximity_plugin = Path(
    "android/app/src/main/java/ru/svoi/mobile/NativeProximityPlugin.java"
)
proximity_plugin.write_text(r'''package ru.svoi.mobile;

import android.content.Context;
import android.os.PowerManager;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "NativeProximity")
public class NativeProximityPlugin extends Plugin {
    private PowerManager.WakeLock proximityWakeLock;

    private boolean proximitySupported() {
        try {
            PowerManager powerManager = (PowerManager)
                getContext().getSystemService(Context.POWER_SERVICE);
            return powerManager != null
                && powerManager.isWakeLockLevelSupported(
                    PowerManager.PROXIMITY_SCREEN_OFF_WAKE_LOCK
                );
        } catch (Exception ignored) {
            return false;
        }
    }

    private PowerManager.WakeLock wakeLock() {
        if (proximityWakeLock == null && proximitySupported()) {
            PowerManager powerManager = (PowerManager)
                getContext().getSystemService(Context.POWER_SERVICE);
            proximityWakeLock = powerManager.newWakeLock(
                PowerManager.PROXIMITY_SCREEN_OFF_WAKE_LOCK,
                "svoi:proximity-screen-off"
            );
            proximityWakeLock.setReferenceCounted(false);
        }
        return proximityWakeLock;
    }

    @PluginMethod
    public void isAvailable(PluginCall call) {
        JSObject result = new JSObject();
        result.put("available", proximitySupported());
        result.put(
            "enabled",
            proximityWakeLock != null && proximityWakeLock.isHeld()
        );
        call.resolve(result);
    }

    @PluginMethod
    public void enable(PluginCall call) {
        PowerManager.WakeLock lock = wakeLock();
        if (lock == null) {
            call.reject("Датчик приближения недоступен");
            return;
        }

        try {
            if (!lock.isHeld()) {
                lock.acquire();
            }
            JSObject result = new JSObject();
            result.put("enabled", true);
            call.resolve(result);
        } catch (Exception error) {
            call.reject(
                error.getMessage() != null
                    ? error.getMessage()
                    : "Не удалось включить датчик приближения"
            );
        }
    }

    @PluginMethod
    public void disable(PluginCall call) {
        releaseWakeLock();
        JSObject result = new JSObject();
        result.put("enabled", false);
        call.resolve(result);
    }

    private void releaseWakeLock() {
        try {
            if (proximityWakeLock != null && proximityWakeLock.isHeld()) {
                proximityWakeLock.release(
                    PowerManager.RELEASE_FLAG_WAIT_FOR_NO_PROXIMITY
                );
            }
        } catch (Exception ignored) {
            try {
                if (proximityWakeLock != null
                        && proximityWakeLock.isHeld()) {
                    proximityWakeLock.release();
                }
            } catch (Exception ignoredAgain) {
            }
        }
    }

    @Override
    protected void handleOnDestroy() {
        releaseWakeLock();
        proximityWakeLock = null;
        super.handleOnDestroy();
    }
}
''')

screen_service = Path(
    "android/app/src/main/java/ru/svoi/mobile/ScreenShareService.java"
)
screen_service.write_text(r'''package ru.svoi.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;

public class ScreenShareService extends Service {
    private static final String CHANNEL_ID = "svoi_screen_share";
    private static final int NOTIFICATION_ID = 4401;

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager manager =
            (NotificationManager) getSystemService(NOTIFICATION_SERVICE);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Демонстрация экрана",
                NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription(
                "Показывается, пока «Свои» демонстрирует экран"
            );
            manager.createNotificationChannel(channel);
        }

        Notification notification = new NotificationCompat.Builder(
            this,
            CHANNEL_ID
        )
            .setContentTitle("Свои")
            .setContentText("Идёт демонстрация экрана")
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build();

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
            );
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
''')

screen_plugin = Path(
    "android/app/src/main/java/ru/svoi/mobile/NativeScreenSharePlugin.java"
)
screen_plugin.write_text(r'''package ru.svoi.mobile;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.DisplayMetrics;

import androidx.activity.result.ActivityResult;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONArray;
import org.json.JSONObject;
import org.webrtc.DataChannel;
import org.webrtc.DefaultVideoDecoderFactory;
import org.webrtc.DefaultVideoEncoderFactory;
import org.webrtc.EglBase;
import org.webrtc.IceCandidate;
import org.webrtc.MediaConstraints;
import org.webrtc.MediaStream;
import org.webrtc.PeerConnection;
import org.webrtc.PeerConnectionFactory;
import org.webrtc.RtpReceiver;
import org.webrtc.ScreenCapturerAndroid;
import org.webrtc.SdpObserver;
import org.webrtc.SessionDescription;
import org.webrtc.SurfaceTextureHelper;
import org.webrtc.VideoCapturer;
import org.webrtc.VideoSource;
import org.webrtc.VideoTrack;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@CapacitorPlugin(name = "NativeScreenShare")
public class NativeScreenSharePlugin extends Plugin {
    private static final String VIDEO_TRACK_ID = "svoi-screen-video";

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Map<String, PeerState> peers = new HashMap<>();

    private PeerConnectionFactory factory;
    private EglBase eglBase;
    private SurfaceTextureHelper textureHelper;
    private VideoSource videoSource;
    private VideoTrack videoTrack;
    private VideoCapturer capturer;
    private boolean captureActive = false;

    @PluginMethod
    public void isAvailable(PluginCall call) {
        JSObject result = new JSObject();
        result.put("available", Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP);
        result.put("active", captureActive);
        call.resolve(result);
    }

    @PluginMethod
    public void startCapture(PluginCall call) {
        if (captureActive) {
            JSObject result = new JSObject();
            result.put("active", true);
            call.resolve(result);
            return;
        }

        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.LOLLIPOP) {
            call.reject("Android не поддерживает MediaProjection");
            return;
        }

        MediaProjectionManager manager = (MediaProjectionManager)
            getContext().getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        Intent intent = manager.createScreenCaptureIntent();
        startActivityForResult(call, intent, "capturePermissionResult");
    }

    @ActivityCallback
    private void capturePermissionResult(
        PluginCall call,
        ActivityResult result
    ) {
        if (call == null) {
            return;
        }
        if (result.getResultCode() != Activity.RESULT_OK
                || result.getData() == null) {
            call.reject("Демонстрация экрана отменена");
            return;
        }

        try {
            startProjectionService();
            initializeFactoryIfNeeded();

            final Intent permissionData = result.getData();
            capturer = new ScreenCapturerAndroid(
                permissionData,
                new MediaProjection.Callback() {
                    @Override
                    public void onStop() {
                        mainHandler.post(() -> stopAllInternal(true));
                    }
                }
            );

            DisplayMetrics metrics = getContext()
                .getResources()
                .getDisplayMetrics();

            int width = Math.max(360, metrics.widthPixels);
            int height = Math.max(640, metrics.heightPixels);

            int maxSide = Math.max(width, height);
            if (maxSide > 1280) {
                double scale = 1280.0 / maxSide;
                width = Math.max(360, (int) Math.round(width * scale));
                height = Math.max(640, (int) Math.round(height * scale));
            }

            textureHelper = SurfaceTextureHelper.create(
                "SvoiScreenCapture",
                eglBase.getEglBaseContext()
            );
            videoSource = factory.createVideoSource(true);
            capturer.initialize(
                textureHelper,
                getContext(),
                videoSource.getCapturerObserver()
            );
            capturer.startCapture(width, height, 20);

            videoTrack = factory.createVideoTrack(
                VIDEO_TRACK_ID,
                videoSource
            );
            videoTrack.setEnabled(true);
            captureActive = true;

            JSObject response = new JSObject();
            response.put("active", true);
            response.put("width", width);
            response.put("height", height);
            call.resolve(response);
        } catch (Exception error) {
            stopAllInternal(false);
            call.reject(
                error.getMessage() != null
                    ? error.getMessage()
                    : "Не удалось запустить захват экрана"
            );
        }
    }

    @PluginMethod
    public void createOffer(PluginCall call) {
        if (!captureActive || videoTrack == null || factory == null) {
            call.reject("Сначала разреши демонстрацию экрана");
            return;
        }

        String peerKey = call.getString("peerKey");
        if (peerKey == null || peerKey.trim().isEmpty()) {
            call.reject("peerKey is required");
            return;
        }

        try {
            removePeerInternal(peerKey);
            List<PeerConnection.IceServer> iceServers =
                parseIceServers(call.getArray("iceServers"));

            PeerState state = new PeerState(peerKey);
            state.pc = factory.createPeerConnection(
                new PeerConnection.RTCConfiguration(iceServers),
                createPeerObserver(state)
            );

            if (state.pc == null) {
                call.reject("Не удалось создать WebRTC соединение");
                return;
            }

            state.pc.addTrack(videoTrack);
            state.pendingOfferCall = call;
            peers.put(peerKey, state);

            state.pc.createOffer(
                new SimpleSdpObserver() {
                    @Override
                    public void onCreateSuccess(SessionDescription sdp) {
                        state.pc.setLocalDescription(
                            new SimpleSdpObserver() {
                                @Override
                                public void onSetSuccess() {
                                    state.localDescriptionSet = true;
                                    maybeResolveOffer(state);
                                }

                                @Override
                                public void onSetFailure(String error) {
                                    rejectOffer(state, error);
                                }
                            },
                            sdp
                        );
                    }

                    @Override
                    public void onCreateFailure(String error) {
                        rejectOffer(state, error);
                    }
                },
                new MediaConstraints()
            );

            mainHandler.postDelayed(
                () -> {
                    if (state.pendingOfferCall != null) {
                        resolveOffer(state);
                    }
                },
                5000
            );
        } catch (Exception error) {
            removePeerInternal(peerKey);
            call.reject(
                error.getMessage() != null
                    ? error.getMessage()
                    : "Не удалось создать демонстрацию"
            );
        }
    }

    @PluginMethod
    public void setAnswer(PluginCall call) {
        String peerKey = call.getString("peerKey");
        String sdp = call.getString("sdp");
        PeerState state = peers.get(peerKey);

        if (state == null || state.pc == null) {
            call.reject("Соединение демонстрации не найдено");
            return;
        }
        if (sdp == null || sdp.isEmpty()) {
            call.reject("SDP answer is required");
            return;
        }

        state.pc.setRemoteDescription(
            new SimpleSdpObserver() {
                @Override
                public void onSetSuccess() {
                    call.resolve();
                }

                @Override
                public void onSetFailure(String error) {
                    call.reject(error);
                }
            },
            new SessionDescription(
                SessionDescription.Type.ANSWER,
                sdp
            )
        );
    }

    @PluginMethod
    public void removePeer(PluginCall call) {
        String peerKey = call.getString("peerKey");
        if (peerKey != null) {
            removePeerInternal(peerKey);
        }
        call.resolve();
    }

    @PluginMethod
    public void stopCapture(PluginCall call) {
        stopAllInternal(false);
        call.resolve();
    }

    @Override
    protected void handleOnDestroy() {
        stopAllInternal(false);
        if (factory != null) {
            factory.dispose();
            factory = null;
        }
        if (eglBase != null) {
            eglBase.release();
            eglBase = null;
        }
        super.handleOnDestroy();
    }

    private void initializeFactoryIfNeeded() {
        if (factory != null) {
            return;
        }

        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions
                .builder(getContext())
                .createInitializationOptions()
        );

        eglBase = EglBase.create();

        DefaultVideoEncoderFactory encoderFactory =
            new DefaultVideoEncoderFactory(
                eglBase.getEglBaseContext(),
                true,
                true
            );
        DefaultVideoDecoderFactory decoderFactory =
            new DefaultVideoDecoderFactory(
                eglBase.getEglBaseContext()
            );

        factory = PeerConnectionFactory.builder()
            .setVideoEncoderFactory(encoderFactory)
            .setVideoDecoderFactory(decoderFactory)
            .createPeerConnectionFactory();
    }

    private List<PeerConnection.IceServer> parseIceServers(
        JSArray input
    ) {
        List<PeerConnection.IceServer> result = new ArrayList<>();

        if (input != null) {
            try {
                JSONArray items = input;
                for (int i = 0; i < items.length(); i++) {
                    JSONObject item = items.optJSONObject(i);
                    if (item == null) {
                        continue;
                    }

                    List<String> urls = new ArrayList<>();
                    Object rawUrls = item.opt("urls");
                    if (rawUrls instanceof JSONArray) {
                        JSONArray urlArray = (JSONArray) rawUrls;
                        for (int j = 0; j < urlArray.length(); j++) {
                            String value = urlArray.optString(j, "");
                            if (!value.isEmpty()) {
                                urls.add(value);
                            }
                        }
                    } else if (rawUrls instanceof String) {
                        String value = (String) rawUrls;
                        if (!value.isEmpty()) {
                            urls.add(value);
                        }
                    }

                    if (urls.isEmpty()) {
                        continue;
                    }

                    PeerConnection.IceServer.Builder builder =
                        PeerConnection.IceServer.builder(urls);

                    String username = item.optString("username", "");
                    String credential = item.optString("credential", "");
                    if (!username.isEmpty()) {
                        builder.setUsername(username);
                    }
                    if (!credential.isEmpty()) {
                        builder.setPassword(credential);
                    }
                    result.add(builder.createIceServer());
                }
            } catch (Exception ignored) {
            }
        }

        if (result.isEmpty()) {
            result.add(
                PeerConnection.IceServer
                    .builder("stun:stun.l.google.com:19302")
                    .createIceServer()
            );
            result.add(
                PeerConnection.IceServer
                    .builder("stun:stun1.l.google.com:19302")
                    .createIceServer()
            );
        }

        return result;
    }

    private PeerConnection.Observer createPeerObserver(
        PeerState state
    ) {
        return new PeerConnection.Observer() {
            @Override
            public void onSignalingChange(
                PeerConnection.SignalingState newState
            ) {}

            @Override
            public void onIceConnectionChange(
                PeerConnection.IceConnectionState newState
            ) {}

            @Override
            public void onIceConnectionReceivingChange(
                boolean receiving
            ) {}

            @Override
            public void onIceGatheringChange(
                PeerConnection.IceGatheringState newState
            ) {
                if (newState == PeerConnection.IceGatheringState.COMPLETE) {
                    maybeResolveOffer(state);
                }
            }

            @Override
            public void onIceCandidate(IceCandidate candidate) {}

            @Override
            public void onIceCandidatesRemoved(
                IceCandidate[] candidates
            ) {}

            @Override
            public void onAddStream(MediaStream stream) {}

            @Override
            public void onRemoveStream(MediaStream stream) {}

            @Override
            public void onDataChannel(DataChannel channel) {}

            @Override
            public void onRenegotiationNeeded() {}

            @Override
            public void onAddTrack(
                RtpReceiver receiver,
                MediaStream[] mediaStreams
            ) {}
        };
    }

    private void maybeResolveOffer(PeerState state) {
        if (!state.localDescriptionSet
                || state.pc == null
                || state.pendingOfferCall == null) {
            return;
        }
        if (state.pc.iceGatheringState()
                == PeerConnection.IceGatheringState.COMPLETE) {
            resolveOffer(state);
        }
    }

    private void resolveOffer(PeerState state) {
        PluginCall call = state.pendingOfferCall;
        if (call == null || state.pc == null) {
            return;
        }
        state.pendingOfferCall = null;

        SessionDescription local = state.pc.getLocalDescription();
        if (local == null || local.description == null) {
            call.reject("Не удалось получить SDP демонстрации");
            return;
        }

        JSObject response = new JSObject();
        response.put("type", "offer");
        response.put("sdp", local.description);
        call.resolve(response);
    }

    private void rejectOffer(PeerState state, String error) {
        PluginCall call = state.pendingOfferCall;
        state.pendingOfferCall = null;
        removePeerInternal(state.peerKey);
        if (call != null) {
            call.reject(
                error != null ? error : "Ошибка WebRTC демонстрации"
            );
        }
    }

    private void removePeerInternal(String peerKey) {
        PeerState state = peers.remove(peerKey);
        if (state != null && state.pc != null) {
            try {
                state.pc.close();
            } catch (Exception ignored) {
            }
            try {
                state.pc.dispose();
            } catch (Exception ignored) {
            }
            state.pc = null;
        }
    }

    private void stopAllInternal(boolean systemStopped) {
        for (String key : new ArrayList<>(peers.keySet())) {
            removePeerInternal(key);
        }

        captureActive = false;

        if (capturer != null) {
            try {
                capturer.stopCapture();
            } catch (Exception ignored) {
            }
            try {
                capturer.dispose();
            } catch (Exception ignored) {
            }
            capturer = null;
        }

        if (videoTrack != null) {
            try {
                videoTrack.dispose();
            } catch (Exception ignored) {
            }
            videoTrack = null;
        }

        if (videoSource != null) {
            try {
                videoSource.dispose();
            } catch (Exception ignored) {
            }
            videoSource = null;
        }

        if (textureHelper != null) {
            try {
                textureHelper.dispose();
            } catch (Exception ignored) {
            }
            textureHelper = null;
        }

        try {
            getContext().stopService(
                new Intent(getContext(), ScreenShareService.class)
            );
        } catch (Exception ignored) {
        }

        if (systemStopped) {
            JSObject event = new JSObject();
            event.put("stopped", true);
            notifyListeners("captureStopped", event);
        }
    }

    private void startProjectionService() {
        Intent service = new Intent(
            getContext(),
            ScreenShareService.class
        );
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            getContext().startForegroundService(service);
        } else {
            getContext().startService(service);
        }
    }

    private static class PeerState {
        final String peerKey;
        PeerConnection pc;
        PluginCall pendingOfferCall;
        boolean localDescriptionSet = false;

        PeerState(String peerKey) {
            this.peerKey = peerKey;
        }
    }

    private static class SimpleSdpObserver implements SdpObserver {
        @Override
        public void onCreateSuccess(SessionDescription sdp) {}

        @Override
        public void onSetSuccess() {}

        @Override
        public void onCreateFailure(String error) {}

        @Override
        public void onSetFailure(String error) {}
    }
}
''')

print("Android permissions, updater and native screen sharing patched.")

