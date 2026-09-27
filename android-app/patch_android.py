from pathlib import Path

manifest = Path("android/app/src/main/AndroidManifest.xml")
text = manifest.read_text()

permissions = """    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
"""

if "android.permission.CAMERA" not in text:
    start = text.find(">", text.find("<manifest"))
    if start == -1:
        raise SystemExit("AndroidManifest.xml: manifest tag not found")
    text = text[: start + 1] + "\n" + permissions + text[start + 1 :]

manifest.write_text(text)

main_activity = Path(
    "android/app/src/main/java/ru/svoi/mobile/MainActivity.java"
)

main_activity.write_text(r'''package ru.svoi.mobile;

import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

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

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        checkForUpdates();
    }

    private int currentVersionCode() {
        try {
            return getPackageManager()
                .getPackageInfo(getPackageName(), 0)
                .versionCode;
        } catch (Exception ignored) {
            return 1;
        }
    }

    private void checkForUpdates() {
        new Thread(() -> {
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection)
                    new URL(LATEST_RELEASE).openConnection();
                connection.setConnectTimeout(5000);
                connection.setReadTimeout(5000);
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

                runOnUiThread(() -> new AlertDialog.Builder(this)
                    .setTitle("Доступно обновление")
                    .setMessage(
                        "Новая версия «Свои»: " + label +
                        "\n\nУстановленная версия продолжит работать, " +
                        "но лучше обновиться."
                    )
                    .setNegativeButton("Позже", null)
                    .setPositiveButton("Обновить", (dialog, which) -> {
                        Intent intent = new Intent(
                            Intent.ACTION_VIEW,
                            Uri.parse(url)
                        );
                        startActivity(intent);
                    })
                    .show()
                );
            } catch (Exception ignored) {
                // Update checks must never block app startup.
            } finally {
                if (connection != null) {
                    connection.disconnect();
                }
            }
        }).start();
    }
}
''')

print("Android permissions and update checker patched.")
