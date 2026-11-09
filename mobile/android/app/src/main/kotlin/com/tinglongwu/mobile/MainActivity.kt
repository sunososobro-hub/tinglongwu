package com.tinglongwu.mobile

import android.os.Bundle
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Subtitle display: never let the screen sleep while the app is in front.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "tlwu/native")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    // App-specific external files dir, e.g.
                    // /storage/emulated/0/Android/data/com.tinglongwu.mobile/files
                    "externalFilesDir" -> {
                        val dir = getExternalFilesDir(null)
                        if (dir == null) result.error("NO_DIR", "external files dir unavailable", null)
                        else result.success(dir.absolutePath)
                    }
                    else -> result.notImplemented()
                }
            }
    }
}
