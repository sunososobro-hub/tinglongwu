package com.tinglongwu.mobile

import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val importer by lazy { ModelImporter(this) }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Subtitle display: never let the screen sleep while the app is in front.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val ch = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "tlwu/native")
        ch.setMethodCallHandler { call, result ->
                when (call.method) {
                    // App-specific external files dir, e.g.
                    // /storage/emulated/0/Android/data/com.tinglongwu.mobile/files
                    "externalFilesDir" -> {
                        val dir = getExternalFilesDir(null)
                        if (dir == null) result.error("NO_DIR", "external files dir unavailable", null)
                        else result.success(dir.absolutePath)
                    }
                    // Cheap thermal/battery reading: battery temp (deg C), level %,
                    // PowerManager thermal status (0 none .. 6 shutdown, -1 n/a).
                    "stats" -> {
                        val i = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
                        val temp = (i?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) ?: 0) / 10.0
                        val level = i?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
                        var thermal = -1
                        if (Build.VERSION.SDK_INT >= 29) {
                            val pm = getSystemService(POWER_SERVICE) as PowerManager
                            thermal = pm.currentThermalStatus
                        }
                        result.success(mapOf("tempC" to temp, "level" to level, "thermal" to thermal))
                    }
                    // Model import (Storage Access Framework: no storage or INTERNET permission).
                    "pickDocuments" -> importer.pick(result)
                    "importDocument" -> importer.import(call, result) { done, total ->
                        ch.invokeMethod("importProgress", mapOf("done" to done, "total" to total))
                    }
                    "cancelImport" -> { importer.cancel(); result.success(null) }
                    "freeBytes" -> result.success(importer.freeBytes(call.argument<String>("dir")!!))
                    // Opens a link in the user's browser (the browser downloads; this app stays offline).
                    "openUrl" -> {
                        try {
                            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(call.argument<String>("url"))))
                            result.success(true)
                        } catch (e: Exception) {
                            result.success(false)
                        }
                    }
                    else -> result.notImplemented()
                }
            }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (!importer.onActivityResult(requestCode, resultCode, data)) {
            super.onActivityResult(requestCode, resultCode, data)
        }
    }
}
