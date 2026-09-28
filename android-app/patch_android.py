from pathlib import Path

manifest = Path("android/app/src/main/AndroidManifest.xml")
text = manifest.read_text()

permissions = """    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
    <uses-permission android:name="android.permission.VIBRATE" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.USE_FULL_SCREEN_INTENT" />
    <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />
    <uses-permission android:name="android.permission.WAKE_LOCK" />
    <uses-permission android:name="android.permission.READ_CONTACTS" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_MICROPHONE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_CAMERA" />
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
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.USE_FULL_SCREEN_INTENT",
        "android.permission.REQUEST_INSTALL_PACKAGES",
        "android.permission.VIBRATE",
        "android.permission.WAKE_LOCK",
        "android.permission.READ_CONTACTS",
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.FOREGROUND_SERVICE_MICROPHONE",
        "android.permission.FOREGROUND_SERVICE_CAMERA",
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
call_service_decl = """        <service
            android:name=".CallForegroundService"
            android:exported="false"
            android:foregroundServiceType="microphone|camera" />
"""
firebase_service_decl = """        <service
            android:name=".SvoiFirebaseMessagingService"
            android:exported="false">
            <intent-filter>
                <action android:name="com.google.firebase.MESSAGING_EVENT" />
            </intent-filter>
        </service>
"""
if 'android:name=".SvoiFirebaseMessagingService"' not in text:
    app_end = text.find("</application>")
    if app_end == -1:
        raise SystemExit("AndroidManifest.xml: application tag not found")
    text = text[:app_end] + firebase_service_decl + text[app_end:]


if 'android:name=".ScreenShareService"' not in text:
    app_end = text.find("</application>")
    if app_end == -1:
        raise SystemExit("AndroidManifest.xml: application tag not found")
    text = text[:app_end] + service_decl + text[app_end:]

if 'android:name=".CallForegroundService"' not in text:
    app_end = text.find("</application>")
    if app_end == -1:
        raise SystemExit("AndroidManifest.xml: application tag not found")
    text = text[:app_end] + call_service_decl + text[app_end:]

call_action_receiver_decl = """        <receiver
            android:name=".CallActionReceiver"
            android:exported="false" />
"""
if 'android:name=".CallActionReceiver"' not in text:
    app_end = text.find("</application>")
    if app_end == -1:
        raise SystemExit("AndroidManifest.xml: application tag not found")
    text = text[:app_end] + call_action_receiver_decl + text[app_end:]

manifest.write_text(text)

# Replace Capacitor launcher icons with the Svoi messenger logo.
res_dir = Path("android/app/src/main/res")
(res_dir / "drawable").mkdir(parents=True, exist_ok=True)
(res_dir / "mipmap-anydpi").mkdir(parents=True, exist_ok=True)
(res_dir / "mipmap-anydpi-v26").mkdir(parents=True, exist_ok=True)
(res_dir / "values").mkdir(parents=True, exist_ok=True)

(res_dir / "values" / "svoi_icon_colors.xml").write_text(r'''<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="svoi_icon_bg">#0757E8</color>
</resources>
''')

(res_dir / "drawable" / "svoi_launcher_foreground.xml").write_text(r'''<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="192"
    android:viewportHeight="192">
    <path
        android:fillColor="#18DDF8"
        android:pathData="M112,57h22c24,0 43,18 43,40v11c0,17 -11,32 -27,39l-1,19 -18,-15h-26c-21,0 -38,-16 -41,-36c11,4 23,7 36,7h31c11,0 20,-8 20,-18V92c0,-10 -9,-18 -20,-18h-19z"/>
    <path
        android:fillColor="#F6FCFF"
        android:pathData="M82,38h20c27,0 49,20 49,45v12c0,25 -22,45 -49,45H70l-28,22 5,-29c-18,-8 -30,-25 -30,-44v-6c0,-25 22,-45 49,-45h16zM83,64H67c-14,0 -25,10 -25,23v4c0,13 11,23 25,23h37c14,0 25,-10 25,-23v-4c0,-13 -11,-23 -25,-23H83z"/>
</vector>
''')

(res_dir / "drawable" / "svoi_launcher.xml").write_text(r'''<?xml version="1.0" encoding="utf-8"?>
<layer-list xmlns:android="http://schemas.android.com/apk/res/android">
    <item>
        <shape android:shape="rectangle">
            <gradient
                android:angle="45"
                android:startColor="#071B60"
                android:centerColor="#0757E8"
                android:endColor="#23E9FF"/>
            <corners android:radius="24dp"/>
        </shape>
    </item>
    <item
        android:drawable="@drawable/svoi_launcher_foreground"
        android:gravity="center"/>
</layer-list>
''')

for name in ["ic_launcher", "ic_launcher_round"]:
    (res_dir / "mipmap-anydpi" / f"{name}.xml").write_text(r'''<?xml version="1.0" encoding="utf-8"?>
<layer-list xmlns:android="http://schemas.android.com/apk/res/android">
    <item android:drawable="@drawable/svoi_launcher"/>
</layer-list>
''')
    (res_dir / "mipmap-anydpi-v26" / f"{name}.xml").write_text(r'''<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/svoi_icon_bg"/>
    <foreground android:drawable="@drawable/svoi_launcher_foreground"/>
</adaptive-icon>
''')

google_services_source = Path("google-services.json")
google_services_target = Path("android/app/google-services.json")
if not google_services_source.exists():
    raise SystemExit("google-services.json not found")
google_services_target.write_text(google_services_source.read_text())

root_gradle = Path("android/build.gradle")
root_gradle_text = root_gradle.read_text()
google_services_classpath = "classpath 'com.google.gms:google-services:4.4.2'"
if google_services_classpath not in root_gradle_text:
    marker = "dependencies {"
    pos = root_gradle_text.find(marker)
    if pos == -1:
        raise SystemExit("android/build.gradle: dependencies block not found")
    pos += len(marker)
    root_gradle_text = (
        root_gradle_text[:pos]
        + "\n        "
        + google_services_classpath
        + root_gradle_text[pos:]
    )
root_gradle.write_text(root_gradle_text)

gradle = Path("android/app/build.gradle")
gradle_text = gradle.read_text()
if "com.google.gms.google-services" not in gradle_text:
    gradle_text += "\napply plugin: 'com.google.gms.google-services'\n"
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
firebase_messaging_dependency = "implementation 'com.google.firebase:firebase-messaging:24.1.0'"
if firebase_messaging_dependency not in gradle_text:
    marker = "dependencies {"
    pos = gradle_text.find(marker)
    if pos == -1:
        raise SystemExit("android/app/build.gradle: dependencies block not found")
    pos += len(marker)
    gradle_text = (
        gradle_text[:pos]
        + "\n    "
        + firebase_messaging_dependency
        + gradle_text[pos:]
    )

badger_dependency = "implementation 'me.leolin:ShortcutBadger:1.1.22@aar'"
if badger_dependency not in gradle_text:
    marker = "dependencies {"
    pos = gradle_text.find(marker)
    if pos == -1:
        raise SystemExit("android/app/build.gradle: dependencies block not found")
    pos += len(marker)
    gradle_text = (
        gradle_text[:pos]
        + "\n    "
        + badger_dependency
        + gradle_text[pos:]
    )

gradle.write_text(gradle_text)

version_file = Path(
    "android/app/src/main/java/ru/svoi/mobile/SvoiVersion.java"
)
version_file.write_text(r'''package ru.svoi.mobile;

public final class SvoiVersion {
    public static final int VERSION_CODE = 1;
    public static final String VERSION_NAME = "debug";
    private SvoiVersion() {}
}
''')

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
import android.view.WindowManager;
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
    private static final String APP_URL =
        "https://epl-gruz.duckdns.org";
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
        registerPlugin(NativeAudioRoutePlugin.class);
        registerPlugin(NativeCallServicePlugin.class);
        registerPlugin(NativeContactsPlugin.class);
        registerPlugin(NativeVibrationPlugin.class);
        registerPlugin(NativeBadgePlugin.class);
        registerPlugin(NativePushPlugin.class);
        super.onCreate(savedInstanceState);

        updaterPrefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        handlePushIntent(getIntent());
        registerDownloadReceiver();
        resumePendingUpdate();
        checkForUpdates();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handlePushIntent(intent);
    }

    private void handlePushIntent(Intent intent) {
        if (intent == null) {
            return;
        }

        boolean incomingCall = intent.getBooleanExtra(
            "svoi_incoming_call",
            false
        );

        if (incomingCall) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
                setShowWhenLocked(true);
                setTurnScreenOn(true);
            } else {
                getWindow().addFlags(
                    WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                        | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                );
            }
            getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            );
        }

        String path = intent.getStringExtra("svoi_url");
        if (path != null && path.startsWith("/") && bridge != null) {
            bridge.getWebView().post(() ->
                bridge.getWebView().loadUrl(APP_URL + path)
            );
        }
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
        return SvoiVersion.VERSION_CODE;
    }

    private String currentVersionName() {
        return SvoiVersion.VERSION_NAME;
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

                int installedCode = currentVersionCode();
                if (latestCode <= installedCode) {
                    updaterPrefs.edit()
                        .remove(PREF_DOWNLOAD_ID)
                        .remove(PREF_PENDING_INSTALL)
                        .apply();
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




contacts_plugin = Path(
    "android/app/src/main/java/ru/svoi/mobile/NativeContactsPlugin.java"
)
contacts_plugin.write_text(r'''package ru.svoi.mobile;

import android.Manifest;
import android.database.Cursor;
import android.provider.ContactsContract;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.Set;

@CapacitorPlugin(
    name = "NativeContacts",
    permissions = {
        @Permission(
            alias = "contacts",
            strings = {Manifest.permission.READ_CONTACTS}
        )
    }
)
public class NativeContactsPlugin extends Plugin {
    @PluginMethod
    public void isAvailable(PluginCall call) {
        JSObject result = new JSObject();
        result.put("available", true);
        result.put(
            "granted",
            getPermissionState("contacts") == PermissionState.GRANTED
        );
        call.resolve(result);
    }

    @PluginMethod
    public void readHashedContacts(PluginCall call) {
        if (getPermissionState("contacts") != PermissionState.GRANTED) {
            requestPermissionForAlias(
                "contacts",
                call,
                "contactsPermissionCallback"
            );
            return;
        }
        readContacts(call);
    }

    @PermissionCallback
    private void contactsPermissionCallback(PluginCall call) {
        if (getPermissionState("contacts") != PermissionState.GRANTED) {
            call.reject("Доступ к контактам не разрешён");
            return;
        }
        readContacts(call);
    }

    private void readContacts(PluginCall call) {
        JSArray contacts = new JSArray();
        Set<String> seenHashes = new HashSet<>();
        Cursor cursor = null;

        try {
            cursor = getContext().getContentResolver().query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                new String[] {
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                    ContactsContract.CommonDataKinds.Phone.NUMBER
                },
                null,
                null,
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " ASC"
            );

            if (cursor != null) {
                int nameIndex = cursor.getColumnIndex(
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME
                );
                int numberIndex = cursor.getColumnIndex(
                    ContactsContract.CommonDataKinds.Phone.NUMBER
                );

                while (cursor.moveToNext()) {
                    String name = nameIndex >= 0
                        ? cursor.getString(nameIndex)
                        : "";
                    String number = numberIndex >= 0
                        ? cursor.getString(numberIndex)
                        : "";

                    String digits = normalizePhone(number);
                    if (digits == null) {
                        continue;
                    }

                    String hash = phoneHash(digits);
                    if (hash == null || !seenHashes.add(hash)) {
                        continue;
                    }

                    JSObject item = new JSObject();
                    item.put("name", name == null ? "" : name);
                    item.put("hash", hash);
                    item.put(
                        "last4",
                        digits.substring(Math.max(0, digits.length() - 4))
                    );
                    contacts.put(item);
                }
            }

            JSObject result = new JSObject();
            result.put("contacts", contacts);
            result.put("count", contacts.length());
            call.resolve(result);
        } catch (Exception error) {
            call.reject(
                error.getMessage() != null
                    ? error.getMessage()
                    : "Не удалось прочитать телефонную книгу"
            );
        } finally {
            if (cursor != null) {
                try {
                    cursor.close();
                } catch (Exception ignored) {
                }
            }
        }
    }

    private String normalizePhone(String rawValue) {
        if (rawValue == null) {
            return null;
        }

        String raw = rawValue.trim();
        String digits = raw.replaceAll("\\D", "");

        if (raw.startsWith("+")) {
            // Already international.
        } else if (digits.startsWith("00")) {
            digits = digits.substring(2);
        } else if (digits.length() == 11 && digits.startsWith("8")) {
            digits = "7" + digits.substring(1);
        } else if (digits.length() == 10) {
            // Default for the current Russian-language deployment.
            digits = "7" + digits;
        }

        if (digits.length() < 8 || digits.length() > 15) {
            return null;
        }
        return digits;
    }

    private String phoneHash(String digits) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(
                ("svoi-phone-v1:" + digits)
                    .getBytes(StandardCharsets.UTF_8)
            );
            StringBuilder out = new StringBuilder(bytes.length * 2);
            for (byte value : bytes) {
                out.append(String.format("%02x", value & 0xff));
            }
            return out.toString();
        } catch (Exception ignored) {
            return null;
        }
    }
}
''')

badge_plugin = Path(
    "android/app/src/main/java/ru/svoi/mobile/NativeBadgePlugin.java"
)
badge_plugin.write_text(r'''package ru.svoi.mobile;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import me.leolin.shortcutbadger.ShortcutBadger;

@CapacitorPlugin(name = "NativeBadge")
public class NativeBadgePlugin extends Plugin {
    @PluginMethod
    public void setBadge(PluginCall call) {
        int count = Math.max(0, call.getInt("count", 0));
        boolean applied;
        try {
            if (count > 0) {
                applied = ShortcutBadger.applyCount(getContext(), count);
            } else {
                applied = ShortcutBadger.removeCount(getContext());
            }
        } catch (Exception error) {
            call.reject(
                error.getMessage() != null
                    ? error.getMessage()
                    : "Не удалось обновить счётчик приложения"
            );
            return;
        }

        JSObject result = new JSObject();
        result.put("count", count);
        result.put("applied", applied);
        call.resolve(result);
    }
}
''')

push_plugin = Path(
    "android/app/src/main/java/ru/svoi/mobile/NativePushPlugin.java"
)
push_plugin.write_text(r'''package ru.svoi.mobile;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.firebase.messaging.FirebaseMessaging;

@CapacitorPlugin(name = "NativePush")
public class NativePushPlugin extends Plugin {
    public static final String CHANNEL_MESSAGES = "svoi_messages";
    public static final String CHANNEL_SILENT = "svoi_messages_silent";
    public static final String CHANNEL_CALLS = "svoi_calls_v1";

    private void ensureChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = (NotificationManager)
            getContext().getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }

        NotificationChannel messages = new NotificationChannel(
            CHANNEL_MESSAGES,
            "Сообщения",
            NotificationManager.IMPORTANCE_HIGH
        );
        messages.setDescription("Сообщения в «Свои»");
        messages.enableVibration(true);
        messages.setShowBadge(true);
        manager.createNotificationChannel(messages);

        NotificationChannel silent = new NotificationChannel(
            CHANNEL_SILENT,
            "Беззвучные чаты",
            NotificationManager.IMPORTANCE_LOW
        );
        silent.setDescription("Уведомления из чатов с включённым беззвучным режимом");
        silent.setSound(null, null);
        silent.enableVibration(false);
        silent.setShowBadge(true);
        manager.createNotificationChannel(silent);

        NotificationChannel calls = new NotificationChannel(
            CHANNEL_CALLS,
            "Звонки",
            NotificationManager.IMPORTANCE_HIGH
        );
        calls.setDescription("Входящие звонки в «Свои»");
        calls.enableVibration(true);
        calls.setVibrationPattern(new long[]{0, 500, 350, 500, 350, 700});
        calls.setLockscreenVisibility(android.app.Notification.VISIBILITY_PUBLIC);
        calls.setShowBadge(false);
        calls.setSound(
            android.media.RingtoneManager.getDefaultUri(
                android.media.RingtoneManager.TYPE_RINGTONE
            ),
            new android.media.AudioAttributes.Builder()
                .setUsage(android.media.AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                .build()
        );
        manager.createNotificationChannel(calls);
    }

    private boolean notificationPermissionGranted() {
        if (Build.VERSION.SDK_INT < 33) {
            return true;
        }
        return getContext().checkSelfPermission(
            Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED;
    }

    private boolean fullScreenAllowed() {
        if (Build.VERSION.SDK_INT < 34) {
            return true;
        }
        NotificationManager manager = (NotificationManager)
            getContext().getSystemService(Context.NOTIFICATION_SERVICE);
        return manager != null && manager.canUseFullScreenIntent();
    }

    @PluginMethod
    public void register(PluginCall call) {
        ensureChannels();

        if (Build.VERSION.SDK_INT >= 33 && !notificationPermissionGranted()) {
            getActivity().requestPermissions(
                new String[]{Manifest.permission.POST_NOTIFICATIONS},
                9127
            );
        }

        FirebaseMessaging.getInstance().getToken().addOnCompleteListener(task -> {
            if (!task.isSuccessful() || task.getResult() == null) {
                call.reject("Не удалось получить Firebase-токен");
                return;
            }

            JSObject result = new JSObject();
            result.put("token", task.getResult());
            result.put("permission", notificationPermissionGranted());
            result.put("fullScreenAllowed", fullScreenAllowed());
            call.resolve(result);
        });
    }

    @PluginMethod
    public void status(PluginCall call) {
        ensureChannels();
        JSObject result = new JSObject();
        result.put("permission", notificationPermissionGranted());
        result.put("fullScreenAllowed", fullScreenAllowed());
        call.resolve(result);
    }

    @PluginMethod
    public void requestFullScreen(PluginCall call) {
        if (Build.VERSION.SDK_INT < 34 || fullScreenAllowed()) {
            JSObject result = new JSObject();
            result.put("allowed", true);
            call.resolve(result);
            return;
        }
        try {
            Intent intent = new Intent(
                Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                Uri.parse("package:" + getContext().getPackageName())
            );
            getActivity().startActivity(intent);
            JSObject result = new JSObject();
            result.put("allowed", false);
            result.put("openedSettings", true);
            call.resolve(result);
        } catch (Exception error) {
            call.reject("Не удалось открыть настройку полноэкранных уведомлений");
        }
    }

    @PluginMethod
    public void clearCall(PluginCall call) {
        String callId = call.getString("callId", "");
        if (callId == null || callId.isEmpty()) {
            call.reject("callId is required");
            return;
        }
        int notificationId = Math.abs(("incoming-call-" + callId).hashCode());
        NotificationManager manager = (NotificationManager)
            getContext().getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.cancel(notificationId);
        }
        JSObject result = new JSObject();
        result.put("cleared", true);
        call.resolve(result);
    }
}
''')

call_action_receiver = Path(
    "android/app/src/main/java/ru/svoi/mobile/CallActionReceiver.java"
)
call_action_receiver.write_text(r'''package ru.svoi.mobile;

import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

public class CallActionReceiver extends BroadcastReceiver {
    private static final String BASE_URL = "https://epl-gruz.duckdns.org";

    @Override
    public void onReceive(Context context, Intent intent) {
        int notificationId = intent.getIntExtra("notification_id", 0);
        NotificationManager manager = (NotificationManager)
            context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null && notificationId != 0) {
            manager.cancel(notificationId);
        }

        String action = intent.getAction();
        String path = intent.getStringExtra("svoi_url");
        if (path == null || path.isEmpty()) {
            return;
        }

        if ("ru.svoi.mobile.ACCEPT_CALL".equals(action)) {
            String separator = path.contains("?") ? "&" : "?";
            Intent open = new Intent(context, MainActivity.class);
            open.addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_CLEAR_TOP
                    | Intent.FLAG_ACTIVITY_SINGLE_TOP
            );
            open.putExtra(
                "svoi_url",
                path + separator + "native_accept=1"
            );
            open.putExtra("svoi_incoming_call", true);
            context.startActivity(open);
            return;
        }

        if (!"ru.svoi.mobile.REJECT_CALL".equals(action)) {
            return;
        }

        Uri uri = Uri.parse(BASE_URL + path);
        String callId = uri.getQueryParameter("incoming_call");
        String token = uri.getQueryParameter("action_token");
        if (callId == null || token == null) {
            return;
        }

        PendingResult pending = goAsync();
        new Thread(() -> {
            HttpURLConnection connection = null;
            try {
                String endpoint = BASE_URL
                    + "/api/calls/native-action/"
                    + URLEncoder.encode(callId, "UTF-8")
                    + "/reject?token="
                    + URLEncoder.encode(token, "UTF-8");
                connection = (HttpURLConnection) new URL(endpoint).openConnection();
                connection.setRequestMethod("POST");
                connection.setConnectTimeout(5000);
                connection.setReadTimeout(5000);
                connection.setDoOutput(true);
                connection.setFixedLengthStreamingMode(0);
                try (OutputStream output = connection.getOutputStream()) {
                    output.flush();
                }
                connection.getResponseCode();
            } catch (Exception ignored) {
            } finally {
                if (connection != null) {
                    connection.disconnect();
                }
                pending.finish();
            }
        }).start();
    }
}
''')

firebase_service = Path(
    "android/app/src/main/java/ru/svoi/mobile/SvoiFirebaseMessagingService.java"
)
firebase_service.write_text(r'''package ru.svoi.mobile;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.os.Build;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.app.Person;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.util.Map;

import me.leolin.shortcutbadger.ShortcutBadger;

public class SvoiFirebaseMessagingService extends FirebaseMessagingService {
    private void ensureChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = (NotificationManager)
            getSystemService(NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }

        NotificationChannel messages = new NotificationChannel(
            NativePushPlugin.CHANNEL_MESSAGES,
            "Сообщения",
            NotificationManager.IMPORTANCE_HIGH
        );
        messages.setDescription("Сообщения в «Свои»");
        messages.enableVibration(true);
        messages.setShowBadge(true);
        manager.createNotificationChannel(messages);

        NotificationChannel silent = new NotificationChannel(
            NativePushPlugin.CHANNEL_SILENT,
            "Беззвучные чаты",
            NotificationManager.IMPORTANCE_LOW
        );
        silent.setDescription("Уведомления из чатов с включённым беззвучным режимом");
        silent.setSound(null, null);
        silent.enableVibration(false);
        silent.setShowBadge(true);
        manager.createNotificationChannel(silent);

        NotificationChannel calls = new NotificationChannel(
            NativePushPlugin.CHANNEL_CALLS,
            "Звонки",
            NotificationManager.IMPORTANCE_HIGH
        );
        calls.setDescription("Входящие звонки в «Свои»");
        calls.enableVibration(true);
        calls.setVibrationPattern(new long[]{0, 500, 350, 500, 350, 700});
        calls.setLockscreenVisibility(android.app.Notification.VISIBILITY_PUBLIC);
        calls.setShowBadge(false);
        calls.setSound(
            android.media.RingtoneManager.getDefaultUri(
                android.media.RingtoneManager.TYPE_RINGTONE
            ),
            new android.media.AudioAttributes.Builder()
                .setUsage(android.media.AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                .build()
        );
        manager.createNotificationChannel(calls);
    }

    private int parseInt(String value, int fallback) {
        try {
            return Integer.parseInt(value == null ? "" : value);
        } catch (Exception ignored) {
            return fallback;
        }
    }

    @Override
    public void onMessageReceived(RemoteMessage remoteMessage) {
        Map<String, String> data = remoteMessage.getData();
        if (data == null || data.isEmpty()) {
            return;
        }

        ensureChannels();

        String title = data.get("title");
        String body = data.get("body");
        String tag = data.get("tag");
        String url = data.get("url");
        boolean silent = "1".equals(data.get("silent"))
            || "true".equalsIgnoreCase(data.get("silent"));
        boolean isCall = "call".equals(data.get("type"));
        int unread = Math.max(0, parseInt(data.get("unread_count"), 0));

        if (title == null || title.isEmpty()) {
            title = "Свои";
        }
        if (body == null || body.isEmpty()) {
            body = "Новое сообщение";
        }

        int notificationId = tag == null || tag.isEmpty()
            ? (int) (System.currentTimeMillis() & 0x7fffffff)
            : Math.abs(tag.hashCode());

        Intent intent = new Intent(this, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        if (url != null) {
            intent.putExtra("svoi_url", url);
        }
        intent.putExtra("svoi_incoming_call", isCall);
        PendingIntent pendingIntent = PendingIntent.getActivity(
            this,
            notificationId,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        String channel = isCall
            ? NativePushPlugin.CHANNEL_CALLS
            : (
                silent
                    ? NativePushPlugin.CHANNEL_SILENT
                    : NativePushPlugin.CHANNEL_MESSAGES
            );

        NotificationCompat.Builder builder = new NotificationCompat.Builder(
            this,
            channel
        )
            .setSmallIcon(android.R.drawable.sym_action_chat)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setNumber(unread)
            .setOnlyAlertOnce(silent)
            .setPriority(
                isCall
                    ? NotificationCompat.PRIORITY_MAX
                    : (
                        silent
                            ? NotificationCompat.PRIORITY_LOW
                            : NotificationCompat.PRIORITY_HIGH
                    )
            );

        if (isCall) {
            Intent acceptIntent = new Intent(this, CallActionReceiver.class);
            acceptIntent.setAction("ru.svoi.mobile.ACCEPT_CALL");
            acceptIntent.putExtra("svoi_url", url);
            acceptIntent.putExtra("notification_id", notificationId);

            Intent declineIntent = new Intent(this, CallActionReceiver.class);
            declineIntent.setAction("ru.svoi.mobile.REJECT_CALL");
            declineIntent.putExtra("svoi_url", url);
            declineIntent.putExtra("notification_id", notificationId);

            PendingIntent acceptPendingIntent = PendingIntent.getBroadcast(
                this,
                notificationId ^ 0x13579BDF,
                acceptIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
            );
            PendingIntent declinePendingIntent = PendingIntent.getBroadcast(
                this,
                notificationId ^ 0x2468ACE0,
                declineIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
            );

            String callerName = body.startsWith("Звонит ")
                ? body.substring("Звонит ".length())
                : body;
            Person caller = new Person.Builder()
                .setName(callerName)
                .setImportant(true)
                .build();

            builder
                .setStyle(
                    NotificationCompat.CallStyle.forIncomingCall(
                        caller,
                        declinePendingIntent,
                        acceptPendingIntent
                    )
                )
                .setCategory(NotificationCompat.CATEGORY_CALL)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .setAutoCancel(false)
                .setFullScreenIntent(pendingIntent, true)
                .setTimeoutAfter(45000L)
                .setOnlyAlertOnce(false)
                .setVibrate(new long[]{0, 500, 350, 500, 350, 700});
        } else {
            builder.setStyle(new NotificationCompat.BigTextStyle().bigText(body));
            if (silent) {
                builder.setSilent(true);
            } else {
                builder.setVibrate(new long[]{0, 180});
            }
        }

        try {
            NotificationManagerCompat.from(this).notify(
                notificationId,
                builder.build()
            );
        } catch (SecurityException ignored) {
        }

        try {
            if (unread > 0) {
                ShortcutBadger.applyCount(this, unread);
            } else {
                ShortcutBadger.removeCount(this);
            }
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onNewToken(String token) {
        super.onNewToken(token);
        getSharedPreferences("svoi_push", MODE_PRIVATE)
            .edit()
            .putString("fcm_token", token)
            .apply();
    }
}
''')

vibration_plugin = Path(
    "android/app/src/main/java/ru/svoi/mobile/NativeVibrationPlugin.java"
)
vibration_plugin.write_text(r'''package ru.svoi.mobile;

import android.content.Context;
import android.os.Build;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "NativeVibration")
public class NativeVibrationPlugin extends Plugin {
    private Vibrator vibrator() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                VibratorManager manager = (VibratorManager)
                    getContext().getSystemService(Context.VIBRATOR_MANAGER_SERVICE);
                return manager == null ? null : manager.getDefaultVibrator();
            }
            return (Vibrator) getContext().getSystemService(Context.VIBRATOR_SERVICE);
        } catch (Exception ignored) {
            return null;
        }
    }

    @PluginMethod
    public void isAvailable(PluginCall call) {
        Vibrator vibrator = vibrator();
        JSObject result = new JSObject();
        result.put("available", vibrator != null && vibrator.hasVibrator());
        call.resolve(result);
    }

    @PluginMethod
    public void vibrate(PluginCall call) {
        Vibrator vibrator = vibrator();
        if (vibrator == null || !vibrator.hasVibrator()) {
            call.reject("Вибрация недоступна");
            return;
        }

        try {
            JSArray input = call.getArray("pattern");
            if (input == null || input.length() == 0) {
                call.reject("Не задан шаблон вибрации");
                return;
            }

            long[] pattern = new long[input.length()];
            for (int i = 0; i < input.length(); i++) {
                pattern[i] = Math.max(0L, input.getLong(i));
            }

            if (pattern.length == 1) {
                long duration = Math.max(1L, pattern[0]);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    vibrator.vibrate(
                        VibrationEffect.createOneShot(
                            duration,
                            VibrationEffect.DEFAULT_AMPLITUDE
                        )
                    );
                } else {
                    vibrator.vibrate(duration);
                }
            } else {
                // Web Vibration API patterns start with vibration duration.
                // Android waveforms start with delay, so prepend a zero delay.
                long[] waveform = new long[pattern.length + 1];
                waveform[0] = 0L;
                System.arraycopy(pattern, 0, waveform, 1, pattern.length);

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    vibrator.vibrate(
                        VibrationEffect.createWaveform(waveform, -1)
                    );
                } else {
                    vibrator.vibrate(waveform, -1);
                }
            }

            call.resolve();
        } catch (Exception error) {
            call.reject(
                error.getMessage() != null
                    ? error.getMessage()
                    : "Не удалось включить вибрацию"
            );
        }
    }

    @PluginMethod
    public void cancel(PluginCall call) {
        Vibrator vibrator = vibrator();
        if (vibrator != null) {
            try {
                vibrator.cancel();
            } catch (Exception ignored) {
            }
        }
        call.resolve();
    }
}
''')

audio_route_plugin = Path(
    "android/app/src/main/java/ru/svoi/mobile/NativeAudioRoutePlugin.java"
)
audio_route_plugin.write_text(r'''package ru.svoi.mobile;

import android.content.Context;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.os.Build;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.util.List;

@CapacitorPlugin(name = "NativeAudioRoute")
public class NativeAudioRoutePlugin extends Plugin {
    private Integer previousMode = null;
    private Boolean previousSpeakerphone = null;

    private AudioManager audioManager() {
        return (AudioManager)
            getContext().getSystemService(Context.AUDIO_SERVICE);
    }

    private void rememberState(AudioManager manager) {
        if (previousMode == null) {
            previousMode = manager.getMode();
        }
        if (previousSpeakerphone == null) {
            previousSpeakerphone = manager.isSpeakerphoneOn();
        }
    }

    private AudioDeviceInfo findCommunicationDevice(
        AudioManager manager,
        int wantedType
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            return null;
        }

        List<AudioDeviceInfo> devices =
            manager.getAvailableCommunicationDevices();

        for (AudioDeviceInfo device : devices) {
            if (device.getType() == wantedType) {
                return device;
            }
        }
        return null;
    }

    private boolean routeSpeaker(AudioManager manager) {
        manager.setMode(AudioManager.MODE_IN_COMMUNICATION);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            AudioDeviceInfo speaker = findCommunicationDevice(
                manager,
                AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
            );
            if (speaker != null && manager.setCommunicationDevice(speaker)) {
                return true;
            }
        }

        manager.setSpeakerphoneOn(true);
        return manager.isSpeakerphoneOn();
    }

    private boolean routeEarpiece(AudioManager manager) {
        manager.setMode(AudioManager.MODE_IN_COMMUNICATION);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            AudioDeviceInfo earpiece = findCommunicationDevice(
                manager,
                AudioDeviceInfo.TYPE_BUILTIN_EARPIECE
            );
            if (earpiece != null && manager.setCommunicationDevice(earpiece)) {
                return true;
            }
            manager.clearCommunicationDevice();
        }

        manager.setSpeakerphoneOn(false);
        return !manager.isSpeakerphoneOn();
    }

    @PluginMethod
    public void setSpeaker(PluginCall call) {
        AudioManager manager = audioManager();
        if (manager == null) {
            call.reject("Аудиосистема Android недоступна");
            return;
        }

        boolean enabled = call.getBoolean("enabled", false);
        rememberState(manager);

        try {
            boolean applied = enabled
                ? routeSpeaker(manager)
                : routeEarpiece(manager);

            JSObject result = new JSObject();
            result.put("speaker", enabled);
            result.put("applied", applied);
            result.put("mode", manager.getMode());
            call.resolve(result);
        } catch (Exception error) {
            call.reject(
                error.getMessage() != null
                    ? error.getMessage()
                    : "Не удалось переключить аудиовыход"
            );
        }
    }

    @PluginMethod
    public void status(PluginCall call) {
        AudioManager manager = audioManager();
        if (manager == null) {
            call.reject("Аудиосистема Android недоступна");
            return;
        }

        JSObject result = new JSObject();
        boolean speaker = manager.isSpeakerphoneOn();

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            AudioDeviceInfo current = manager.getCommunicationDevice();
            if (current != null) {
                speaker = current.getType()
                    == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER;
                result.put("deviceType", current.getType());
                result.put("deviceName", String.valueOf(current.getProductName()));
            }
        }

        result.put("speaker", speaker);
        result.put("mode", manager.getMode());
        call.resolve(result);
    }

    @PluginMethod
    public void reset(PluginCall call) {
        AudioManager manager = audioManager();
        if (manager != null) {
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    manager.clearCommunicationDevice();
                }
                if (previousSpeakerphone != null) {
                    manager.setSpeakerphoneOn(previousSpeakerphone);
                } else {
                    manager.setSpeakerphoneOn(false);
                }
                if (previousMode != null) {
                    manager.setMode(previousMode);
                }
            } catch (Exception ignored) {
            }
        }

        previousMode = null;
        previousSpeakerphone = null;

        JSObject result = new JSObject();
        result.put("reset", true);
        call.resolve(result);
    }

    @Override
    protected void handleOnDestroy() {
        AudioManager manager = audioManager();
        if (manager != null) {
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    manager.clearCommunicationDevice();
                }
                if (previousSpeakerphone != null) {
                    manager.setSpeakerphoneOn(previousSpeakerphone);
                }
                if (previousMode != null) {
                    manager.setMode(previousMode);
                }
            } catch (Exception ignored) {
            }
        }
        previousMode = null;
        previousSpeakerphone = null;
        super.handleOnDestroy();
    }
}
''')

call_service_plugin = Path(
    "android/app/src/main/java/ru/svoi/mobile/NativeCallServicePlugin.java"
)
call_service_plugin.write_text(r'''package ru.svoi.mobile;

import android.content.Context;
import android.content.Intent;
import android.os.Build;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "NativeCallService")
public class NativeCallServicePlugin extends Plugin {
    @PluginMethod
    public void start(PluginCall call) {
        String name = call.getString("name", "Звонок");
        boolean video = call.getBoolean("video", false);
        boolean group = call.getBoolean("group", false);

        Intent intent = new Intent(getContext(), CallForegroundService.class);
        intent.setAction(CallForegroundService.ACTION_START);
        intent.putExtra(CallForegroundService.EXTRA_NAME, name);
        intent.putExtra(CallForegroundService.EXTRA_VIDEO, video);
        intent.putExtra(CallForegroundService.EXTRA_GROUP, group);

        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                getContext().startForegroundService(intent);
            } else {
                getContext().startService(intent);
            }
            JSObject result = new JSObject();
            result.put("active", true);
            call.resolve(result);
        } catch (Exception error) {
            call.reject(
                error.getMessage() != null
                    ? error.getMessage()
                    : "Не удалось запустить фоновый режим звонка"
            );
        }
    }

    @PluginMethod
    public void stop(PluginCall call) {
        Intent intent = new Intent(getContext(), CallForegroundService.class);
        intent.setAction(CallForegroundService.ACTION_STOP);
        try {
            getContext().startService(intent);
        } catch (Exception ignored) {
            try {
                getContext().stopService(
                    new Intent(getContext(), CallForegroundService.class)
                );
            } catch (Exception ignoredAgain) {
            }
        }
        JSObject result = new JSObject();
        result.put("active", false);
        call.resolve(result);
    }
}
''')

call_foreground_service = Path(
    "android/app/src/main/java/ru/svoi/mobile/CallForegroundService.java"
)
call_foreground_service.write_text(r'''package ru.svoi.mobile;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import androidx.core.app.NotificationCompat;

public class CallForegroundService extends Service {
    public static final String ACTION_START = "ru.svoi.mobile.CALL_SERVICE_START";
    public static final String ACTION_STOP = "ru.svoi.mobile.CALL_SERVICE_STOP";
    public static final String EXTRA_NAME = "name";
    public static final String EXTRA_VIDEO = "video";
    public static final String EXTRA_GROUP = "group";

    private static final String CHANNEL_ID = "svoi_active_call_v1";
    private static final int NOTIFICATION_ID = 4601;

    private PowerManager.WakeLock wakeLock;

    @Override
    public void onCreate() {
        super.onCreate();
        ensureChannel();
        acquireWakeLock();
    }

    private void ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = (NotificationManager)
            getSystemService(NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID,
            "Активный звонок",
            NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("Поддерживает активный звонок «Свои» в фоне");
        channel.setShowBadge(false);
        channel.setSound(null, null);
        manager.createNotificationChannel(channel);
    }

    private void acquireWakeLock() {
        try {
            PowerManager manager = (PowerManager)
                getSystemService(POWER_SERVICE);
            if (manager == null) {
                return;
            }
            wakeLock = manager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "svoi:active-call"
            );
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire();
        } catch (Exception ignored) {
        }
    }

    private Notification buildNotification(
        String name,
        boolean video,
        boolean group
    ) {
        Intent open = new Intent(this, MainActivity.class);
        open.addFlags(
            Intent.FLAG_ACTIVITY_CLEAR_TOP
                | Intent.FLAG_ACTIVITY_SINGLE_TOP
        );
        open.putExtra("svoi_restore_active_call", true);

        PendingIntent pendingIntent = PendingIntent.getActivity(
            this,
            4601,
            open,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        String kind = group
            ? "Групповой звонок"
            : (video ? "Видеозвонок" : "Голосовой звонок");

        return new NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle(name == null || name.isEmpty() ? "Свои" : name)
            .setContentText(kind + " · нажмите, чтобы вернуться")
            .setContentIntent(pendingIntent)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .build();
    }

    private int foregroundTypes(boolean video) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            return 0;
        }

        int types = ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;

        if (
            video
            && checkSelfPermission(Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED
        ) {
            types |= ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA;
        }
        return types;
    }

    private void showForeground(
        String name,
        boolean video,
        boolean group
    ) {
        Notification notification = buildNotification(name, video, group);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                foregroundTypes(video)
            );
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }

        String name = intent == null
            ? "Звонок"
            : intent.getStringExtra(EXTRA_NAME);
        boolean video = intent != null
            && intent.getBooleanExtra(EXTRA_VIDEO, false);
        boolean group = intent != null
            && intent.getBooleanExtra(EXTRA_GROUP, false);

        try {
            showForeground(name, video, group);
        } catch (Exception error) {
            stopSelf();
            return START_NOT_STICKY;
        }

        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        try {
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
        } catch (Exception ignored) {
        }
        wakeLock = null;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
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
                        mainHandler.post(() -> {
                            if (captureActive) {
                                stopAllInternal(true);
                            }
                        });
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

