package com.tinglongwu.mobile

import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
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
                    else -> result.notImplemented()
                }
            }
    }
}
