plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// #1511: emulator debug build with x86_64 native libs (opt-in; release APK/AAB stay arm64-v8a only).
val tlwuX86 = System.getenv("TLWU_X86_64") == "1"

android {
    namespace = "com.tinglongwu.mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.tinglongwu.mobile"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        // App label; the debug build overrides it so both apps are told apart on the launcher (#1600).
        manifestPlaceholders["appLabel"] = "聽龍"
        // ROG Phone 6 is arm64 only; drop sherpa's armv7/x86 libs.
        // #1511: TLWU_X86_64=1 also keeps x86_64 for an emulator debug build (needs the x86_64 AAR slice,
        // tool/build_llama_android.sh with TLWU_LLAMA_ABIS="arm64-v8a x86_64"). Release never ships it (below).
        ndk {
            abiFilters += if (tlwuX86) listOf("arm64-v8a", "x86_64") else listOf("arm64-v8a")
        }
    }

    packaging {
        jniLibs {
            excludes += listOf("lib/armeabi-v7a/**", "lib/x86/**")
            if (!tlwuX86) excludes += "lib/x86_64/**"
        }
    }

    // Model packs for Google Play (fast-follow, downloaded by the Play Store). #1169: only when CI sets
    // TLWU_PLAY_PACKS=1 (repo variable PLAY_MODEL_PACKS=true). The internal track gets a new build on every dev push and
    // Play re-sent the whole ~1.3 GB pack with each of them although the pack bytes were identical; without the packs
    // an update is the app alone (models are imported once and stay in the app folder).
    if (System.getenv("TLWU_PLAY_PACKS") == "1") {
        assetPacks += listOf(":models_asr", ":models_llm")
    }

    // #1169: keep the model files STORED (not deflated) in the pack APKs. AGP turns noCompress into the
    // bundle's uncompressedGlob. Deflated multi-GB files give Play's delta patching nothing to chunk against
    // (and a 1.13 GB gguf only shrinks ~2% anyway), so an app-code-only update re-sent the whole 1.3 GB.
    // Every extension in assets/models_manifest.json must be listed (test/asset_pack_config_test.dart).
    androidResources {
        noCompress += listOf("gguf", "onnx", "txt")
    }

    // Release key from the environment (CI decodes the ANDROID_KEYSTORE_B64 secret into a file).
    // Without TLWU_KEYSTORE the release APK is debug-signed, so local builds and forks still work.
    val tlwuKeystore = System.getenv("TLWU_KEYSTORE")?.takeIf { it.isNotBlank() && file(it).exists() }
    signingConfigs {
        if (tlwuKeystore != null) {
            create("tlwuRelease") {
                storeFile = file(tlwuKeystore)
                storePassword = System.getenv("TLWU_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("TLWU_KEY_ALIAS")
                keyPassword = System.getenv("TLWU_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            // #1600: a USB debug build installs next to the Play build (different signing key, so the same
            // applicationId would force an uninstall that also deletes the downloaded models and packs).
            // Only the id changes; namespace (Kotlin package) stays com.tinglongwu.mobile.
            applicationIdSuffix = ".dev"
            versionNameSuffix = "-dev"
            manifestPlaceholders["appLabel"] = "聽龍 (dev)"
        }
        release {
            signingConfig = signingConfigs.getByName(if (tlwuKeystore != null) "tlwuRelease" else "debug")
            // Room/WorkManager keep rules for R8 (#1023).
            proguardFiles("proguard-rules.pro")
        }
    }
}

// #1511: the release variant drops lib/x86_64 even when TLWU_X86_64=1 is set (CI checks the release APK/AAB
// with scripts/check_apk_abis.py).
androidComponents {
    onVariants(selector().withBuildType("release")) { variant ->
        variant.packaging.jniLibs.excludes.add("lib/x86_64/**")
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

dependencies {
    // Play Asset Delivery client (PlayPacks.kt); the download itself is done by the Play Store.
    implementation("com.google.android.play:asset-delivery:2.3.0")
    implementation("androidx.activity:activity:1.10.1")
}
