plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

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
        // ROG Phone 6 is arm64 only; drop sherpa's armv7/x86 libs.
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    packaging {
        jniLibs {
            excludes += listOf("lib/armeabi-v7a/**", "lib/x86/**", "lib/x86_64/**")
        }
    }

    // Model packs for Google Play (on-demand, downloaded by the Play Store, so no INTERNET here).
    assetPacks += listOf(":models_asr", ":models_llm")

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
        release {
            signingConfig = signingConfigs.getByName(if (tlwuKeystore != null) "tlwuRelease" else "debug")
        }
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
