package com.tinglongwu.mobile

import android.Manifest
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.media.projection.MediaProjectionManager
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.WindowManager
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel

// FlutterFragmentActivity (a ComponentActivity) so Play's download confirmation can use an
// activity-result launcher.
class MainActivity : FlutterFragmentActivity() {
    private val importer by lazy { ModelImporter(this) }
    private val playConfirm: ActivityResultLauncher<IntentSenderRequest> =
        registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { r ->
            play.onConfirmResult(r.resultCode == RESULT_OK)
        }
    private val play: PlayPacks by lazy { PlayPacks(this, playConfirm) }

    // Phone-audio capture (#998): MediaProjection consent, overlay permission, notification.
    private var captureResult: MethodChannel.Result? = null
    private val captureConsent: ActivityResultLauncher<Intent> =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
            val res = captureResult
            captureResult = null
            val data = r.data
            if (r.resultCode != RESULT_OK || data == null || Build.VERSION.SDK_INT < 29) {
                res?.success(false)
                return@registerForActivityResult
            }
            startForegroundService(Intent(this, PlaybackCaptureService::class.java)
                .setAction(PlaybackCaptureService.ACTION_START)
                .putExtra(PlaybackCaptureService.EXTRA_CODE, r.resultCode)
                .putExtra(PlaybackCaptureService.EXTRA_DATA, data))
            res?.success(true)
        }
    private var overlayResult: MethodChannel.Result? = null
    private val overlaySettings: ActivityResultLauncher<Intent> =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
            overlayResult?.success(SubtitleOverlay.canDraw(this))
            overlayResult = null
        }
    private val notifPermission: ActivityResultLauncher<String> =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Subtitle display: never let the screen sleep while the app is in front.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    override fun onResume() {
        super.onResume()
        SubtitleOverlay.setAppInFront(true)
    }

    override fun onPause() {
        SubtitleOverlay.setAppInFront(false)
        super.onPause()
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // Phone-audio PCM (16 kHz mono PCM16 chunks); end of stream = capture stopped.
        EventChannel(flutterEngine.dartExecutor.binaryMessenger, "tlwu/playback_pcm")
            .setStreamHandler(object : EventChannel.StreamHandler {
                override fun onListen(arguments: Any?, events: EventChannel.EventSink) {
                    PlaybackCaptureService.sink = { b ->
                        if (b == null) events.endOfStream() else events.success(b)
                    }
                }
                override fun onCancel(arguments: Any?) {
                    PlaybackCaptureService.sink = null
                }
            })
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
                    // Model files inside install-time asset packs (copied into the models dir on first launch).
                    "bundledModels" -> result.success(importer.bundled(call.argument<List<String>>("paths")!!))
                    // Models from Google Play asset packs (only when installed from Play).
                    "playStatus" -> play.status(result)
                    "playFetch" -> play.fetch(result) { done, total, status ->
                        ch.invokeMethod("playProgress", mapOf("done" to done, "total" to total, "status" to status))
                    }
                    "playCancel" -> { play.cancel(); result.success(null) }
                    "playRemove" -> play.remove(result)
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
                    // Other apps' audio + floating subtitles (#998).
                    "playbackCaptureSupported" -> result.success(Build.VERSION.SDK_INT >= 29)
                    "startPlaybackCapture" -> {
                        if (Build.VERSION.SDK_INT < 29) { result.success(false) }
                        else if (captureResult != null) { result.error("BUSY", "consent already pending", null) }
                        else {
                            if (Build.VERSION.SDK_INT >= 33) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                            captureResult = result
                            val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
                            captureConsent.launch(mpm.createScreenCaptureIntent())
                        }
                    }
                    "stopPlaybackCapture" -> {
                        if (PlaybackCaptureService.running) PlaybackCaptureService.stop(this)
                        SubtitleOverlay.hide()
                        result.success(null)
                    }
                    "overlayAllowed" -> result.success(SubtitleOverlay.canDraw(this))
                    "requestOverlayPermission" -> {
                        if (SubtitleOverlay.canDraw(this) || overlayResult != null) {
                            result.success(SubtitleOverlay.canDraw(this))
                        } else {
                            overlayResult = result
                            overlaySettings.launch(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                Uri.parse("package:$packageName")))
                        }
                    }
                    "overlayShow" -> { SubtitleOverlay.show(this); result.success(null) }
                    "overlayHide" -> { SubtitleOverlay.hide(); result.success(null) }
                    "overlayText" -> {
                        val lines = (call.argument<List<Map<String, String?>>>("lines") ?: emptyList())
                            .map { SubtitleOverlay.Line(it["src"], it["tr"]) }
                        SubtitleOverlay.update(lines, call.argument<String>("hint"))
                        result.success(null)
                    }
                    "overlayStyle" -> {
                        SubtitleOverlay.style(call.argument<Double>("fontSize"), call.argument<Double>("opacity"))
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    override fun onDestroy() {
        // The Dart pipeline dies with the activity: no point capturing audio for nobody.
        if (isFinishing && PlaybackCaptureService.running) PlaybackCaptureService.stop(this)
        SubtitleOverlay.hide()
        super.onDestroy()
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (!importer.onActivityResult(requestCode, resultCode, data)) {
            super.onActivityResult(requestCode, resultCode, data)
        }
    }
}
