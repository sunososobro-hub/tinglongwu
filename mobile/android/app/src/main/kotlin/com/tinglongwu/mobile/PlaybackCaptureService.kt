package com.tinglongwu.mobile

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.annotation.RequiresApi

/**
 * Captures other apps' playback audio (AudioPlaybackCapture, Android 10+) for the subtitle
 * pipeline (#998). Runs as a foreground service of type mediaProjection so capture keeps going
 * while the user is in another app. Audio: 16 kHz mono PCM16 (what the ASR isolate expects),
 * pushed to [sink] on the main thread in ~100 ms chunks. Nothing leaves the device.
 */
class PlaybackCaptureService : Service() {
    companion object {
        private const val TAG = "tlwu.capture"
        const val SAMPLE_RATE = 16000
        private const val CHANNEL_ID = "tlwu_capture"
        private const val NOTIF_ID = 998
        const val ACTION_START = "com.tinglongwu.mobile.CAPTURE_START"
        const val ACTION_STOP = "com.tinglongwu.mobile.CAPTURE_STOP"
        const val EXTRA_CODE = "code"
        const val EXTRA_DATA = "data"

        /** Receives PCM chunks; null = capture stopped. Set by the EventChannel listener. */
        @Volatile var sink: ((ByteArray?) -> Unit)? = null
        @Volatile var running = false
            private set

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, PlaybackCaptureService::class.java).setAction(ACTION_STOP))
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private var projection: MediaProjection? = null
    private var record: AudioRecord? = null
    private var thread: Thread? = null
    @Volatile private var stopping = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { shutdown(); return START_NOT_STICKY }
            ACTION_START -> {
                if (Build.VERSION.SDK_INT < 29) { stopSelf(); return START_NOT_STICKY }
                // startForeground must come before getMediaProjection (Android 14+ enforces it).
                startForeground29()
                val code = intent.getIntExtra(EXTRA_CODE, 0)
                @Suppress("DEPRECATION")
                val data: Intent? = if (Build.VERSION.SDK_INT >= 33)
                    intent.getParcelableExtra(EXTRA_DATA, Intent::class.java)
                else intent.getParcelableExtra(EXTRA_DATA)
                if (data == null || !begin(code, data)) shutdown()
            }
        }
        return START_NOT_STICKY
    }

    private fun startForeground29() {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "看影片翻譯", NotificationManager.IMPORTANCE_LOW))
        }
        val open = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1,
            Intent(this, PlaybackCaptureService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE)
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL_ID)
        else @Suppress("DEPRECATION") Notification.Builder(this)
        val n = b.setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("聽龍：看影片翻譯中")
            .setContentText("翻譯手機正在播的影片，點此回到聽龍")
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "停止", stop).build())
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIF_ID, n)
        }
    }

    @SuppressLint("MissingPermission") // RECORD_AUDIO is checked by the Dart side before starting.
    @RequiresApi(29)
    private fun begin(code: Int, data: Intent): Boolean {
        try {
            val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
            val mp = mpm.getMediaProjection(code, data) ?: return false
            projection = mp
            mp.registerCallback(object : MediaProjection.Callback() {
                override fun onStop() { main.post { shutdown() } }
            }, main)
            val cfg = AudioPlaybackCaptureConfiguration.Builder(mp)
                .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                .addMatchingUsage(AudioAttributes.USAGE_GAME)
                .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
                .build()
            val fmt = AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(SAMPLE_RATE)
                .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                .build()
            val minBuf = AudioRecord.getMinBufferSize(
                SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val chunk = SAMPLE_RATE / 10 * 2 // 100 ms of PCM16
            val rec = AudioRecord.Builder()
                .setAudioFormat(fmt)
                .setBufferSizeInBytes(maxOf(minBuf, chunk * 4))
                .setAudioPlaybackCaptureConfig(cfg)
                .build()
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord not initialized")
                rec.release()
                return false
            }
            record = rec
            rec.startRecording()
            running = true
            stopping = false
            thread = Thread({
                val buf = ByteArray(chunk)
                while (!stopping) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n < 0) { Log.e(TAG, "read error $n"); break }
                    if (n == 0) continue
                    val out = buf.copyOf(n)
                    main.post { sink?.invoke(out) }
                }
                if (!stopping) main.post { shutdown() }
            }, "tlwu-capture").also { it.start() }
            Log.i(TAG, "playback capture started")
            return true
        } catch (e: Exception) {
            Log.e(TAG, "capture start failed", e)
            return false
        }
    }

    private fun shutdown() {
        if (stopping && !running) return
        stopping = true
        running = false
        try { thread?.join(500) } catch (_: InterruptedException) {}
        thread = null
        try { record?.stop() } catch (_: Exception) {}
        record?.release()
        record = null
        try { projection?.stop() } catch (_: Exception) {}
        projection = null
        SubtitleOverlay.hide()
        main.post { sink?.invoke(null) }
        if (Build.VERSION.SDK_INT >= 24) stopForeground(STOP_FOREGROUND_REMOVE)
        else @Suppress("DEPRECATION") stopForeground(true)
        stopSelf()
        Log.i(TAG, "playback capture stopped")
    }

    override fun onDestroy() {
        if (running) shutdown()
        super.onDestroy()
    }
}
