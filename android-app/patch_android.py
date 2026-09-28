from pathlib import Path

manifest = Path("android/app/src/main/AndroidManifest.xml")
text = manifest.read_text()

permissions = """    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />
"""

if "android.permission.CAMERA" not in text:
    start = text.find(">", text.find("<manifest"))
    if start == -1:
        raise SystemExit("AndroidManifest.xml: manifest tag not found")
    text = text[: start + 1] + "\n" + permissions + text[start + 1 :]
elif "android.permission.REQUEST_INSTALL_PACKAGES" not in text:
    start = text.find(">", text.find("<manifest"))
    text = (
        text[: start + 1]
        + '\n    <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />\n'
        + text[start + 1 :]
    )

manifest.write_text(text)

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

public class MainActivity extends BridgeActivity {
    private static final String LATEST_RELEASE =
        "https://api.github.com/repos/geodenix/svoi-chat/releases/latest";
    private static final String PREFS = "svoi_updater";
    private static final String PREF_DOWNLOAD_ID = "download_id";
    private static final String PREF_PENDING_INSTALL = "pending_install";

    private SharedPreferences updaterPrefs;
    private BroadcastReceiver downloadReceiver;

    @Override
    public void onCreate(Bundle savedInstanceState) {
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
        if (isFinishing() || isDestroyed()) {
            return;
        }

        new AlertDialog.Builder(this)
            .setTitle("Доступно обновление")
            .setMessage(
                "Новая версия «Свои»: " + versionName +
                "\n\nНажми «Скачать». После загрузки Android " +
                "предложит установить обновление."
            )
            .setNegativeButton("Позже", null)
            .setPositiveButton(
                "Скачать",
                (dialog, which) -> startUpdateDownload(
                    url,
                    versionName,
                    versionCode
                )
            )
            .show();
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
            updaterPrefs.edit()
                .putBoolean(PREF_PENDING_INSTALL, false)
                .apply();

            Intent install = new Intent(Intent.ACTION_VIEW);
            install.setDataAndType(
                apkUri,
                "application/vnd.android.package-archive"
            );
            install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            install.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(install);
        } catch (Exception error) {
            Toast.makeText(
                this,
                "Не удалось открыть установщик обновления",
                Toast.LENGTH_LONG
            ).show();
        }
    }
}
''')

print("Android permissions and native update downloader patched.")
