import re
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("Usage: patch_release.py VERSION_CODE VERSION_NAME")

version_code = int(sys.argv[1])
version_name = sys.argv[2]

gradle = Path("android/app/build.gradle")
text = gradle.read_text()

text = re.sub(
    r'versionCode\s+\d+',
    f'versionCode {version_code}',
    text,
    count=1,
)
text = re.sub(
    r'versionName\s+"[^"]+"',
    f'versionName "{version_name}"',
    text,
    count=1,
)

marker = "// SVOI_RELEASE_SIGNING"
if marker not in text:
    text += r'''

// SVOI_RELEASE_SIGNING
android {
    signingConfigs {
        release {
            storeFile file(System.getenv("SVOI_KEYSTORE_PATH"))
            storePassword System.getenv("SVOI_KEYSTORE_PASSWORD")
            keyAlias System.getenv("SVOI_KEY_ALIAS")
            keyPassword System.getenv("SVOI_KEY_PASSWORD")
        }
    }

    buildTypes {
        release {
            signingConfig signingConfigs.release
        }
    }
}
'''

gradle.write_text(text)

version_file = Path(
    "android/app/src/main/java/ru/svoi/mobile/SvoiVersion.java"
)
version_file.write_text(
    "package ru.svoi.mobile;\n\n"
    "public final class SvoiVersion {\n"
    f"    public static final int VERSION_CODE = {version_code};\n"
    f'    public static final String VERSION_NAME = "{version_name}";\n'
    "    private SvoiVersion() {}\n"
    "}\n"
)

print(f"Release configured: {version_name} ({version_code})")
