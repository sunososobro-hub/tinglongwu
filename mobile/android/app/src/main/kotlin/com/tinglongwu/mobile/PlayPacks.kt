package com.tinglongwu.mobile

import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import com.google.android.play.core.assetpacks.AssetPackException
import com.google.android.play.core.assetpacks.AssetPackManager
import com.google.android.play.core.assetpacks.AssetPackManagerFactory
import com.google.android.play.core.assetpacks.AssetPackState
import com.google.android.play.core.assetpacks.AssetPackStateUpdateListener
import com.google.android.play.core.assetpacks.model.AssetPackErrorCode
import com.google.android.play.core.assetpacks.model.AssetPackStatus
import io.flutter.plugin.common.MethodChannel

/**
 * Models through Google Play Asset Delivery (fast-follow packs `models_asr`, `models_llm`). Play
 * downloads them right after install, so these Play downloads use no app network code. The app reads
 * the model files in place from [locations] (no copy). [fetch] only joins or restarts a download
 * that is not finished yet. A side-loaded APK has no packs: [locations] is empty, `available` is
 * false, and the manual zip import stays the way in.
 */
class PlayPacks(
    activity: android.app.Activity,
    private val confirm: ActivityResultLauncher<IntentSenderRequest>,
) {
    private val apm: AssetPackManager = AssetPackManagerFactory.getInstance(activity.applicationContext)
    private var listener: AssetPackStateUpdateListener? = null
    private var pending: MethodChannel.Result? = null
    private var asked = false
    private var progress: ((Long, Long, String) -> Unit)? = null
    private var lastReport: ((String) -> Unit)? = null

    /** {available, totalBytes, paths: {pack: assetsPath}} ; paths only for packs already on the device. */
    fun status(result: MethodChannel.Result) {
        apm.getPackStates(PACKS)
            .addOnSuccessListener { s ->
                result.success(
                    mapOf("available" to true, "totalBytes" to s.totalBytes(), "paths" to paths())
                )
            }
            .addOnFailureListener { e ->
                // Not installed from Play (or Play Store missing): no packs to fetch.
                result.success(mapOf("available" to false, "error" to (e.message ?: e.toString())))
            }
    }

    /**
     * Joins (or restarts) the Play download of every pack not yet on the device. Progress goes to
     * [onProgress] (bytes, total, state name, see [stateName]). Completes with {pack: assetsPath}
     * once all packs are COMPLETED; fails with code FAILED (details = "network", "storage" or the
     * Play error code) or CANCELLED. Declining Play's mobile-data dialog does not fail: the
     * download keeps waiting for Wi-Fi and [confirmDialog] can ask again.
     */
    fun fetch(result: MethodChannel.Result, onProgress: (Long, Long, String) -> Unit) {
        if (pending != null) { result.error("BUSY", "download already running", null); return }
        if (PACKS.all { apm.getPackLocation(it) != null }) { result.success(paths()); return }
        pending = result
        asked = false
        val states = HashMap<String, AssetPackState>()
        fun report(state: String) {
            progress?.invoke(states.values.sumOf { it.bytesDownloaded() },
                states.values.sumOf { it.totalBytesToDownload() }, state)
        }
        progress = { d, t, st -> onProgress(d, t, st) }
        lastReport = ::report
        val l = AssetPackStateUpdateListener { st ->
            if (st.name() !in PACKS) return@AssetPackStateUpdateListener
            states[st.name()] = st
            report(stateName(st.status()))
            when (st.status()) {
                AssetPackStatus.REQUIRES_USER_CONFIRMATION, AssetPackStatus.WAITING_FOR_WIFI -> {
                    // Large download on mobile data, or Play wants consent: show Play's own dialog once.
                    if (!asked) { asked = true; apm.showConfirmationDialog(confirm) }
                }
                AssetPackStatus.FAILED -> finishError("FAILED", "${st.name()}: Play error ${st.errorCode()}", reason(st.errorCode()))
                AssetPackStatus.CANCELED -> finishError("CANCELLED", "cancelled", null)
                AssetPackStatus.COMPLETED -> {
                    if (PACKS.all { apm.getPackLocation(it) != null }) finishOk()
                }
                else -> {}
            }
        }
        listener = l
        apm.registerListener(l)
        // Seed the total with packs Play already knows (one may be done, the other still running).
        apm.getPackStates(PACKS).addOnSuccessListener { s ->
            for ((name, st) in s.packStates()) if (name in PACKS) states.putIfAbsent(name, st)
            report("pending")
        }
        apm.fetch(PACKS).addOnFailureListener { e ->
            finishError("FAILED", e.message ?: e.toString(), (e as? AssetPackException)?.let { reason(it.errorCode) })
        }
    }

    /** Result of Play's confirmation dialog. Declined = keep waiting for Wi-Fi (Play resumes there). */
    fun onConfirmResult(ok: Boolean) {
        if (!ok) lastReport?.invoke("waitingWifi")
    }

    /** "Use mobile data" from the waiting-for-Wi-Fi screen: shows Play's dialog again. */
    fun confirmDialog() {
        if (pending != null) apm.showConfirmationDialog(confirm)
    }

    fun cancel() { apm.cancel(PACKS) }

    /** {pack: assetsPath} for the packs already on the device (empty for a side-loaded APK). */
    fun locations(): Map<String, String> = try { paths() } catch (_: Exception) { emptyMap() }

    private fun paths(): Map<String, String> =
        PACKS.mapNotNull { p -> apm.getPackLocation(p)?.assetsPath()?.let { p to it } }.toMap()

    private fun finishOk() {
        val r = pending ?: return
        cleanup(); r.success(paths())
    }

    private fun finishError(code: String, msg: String, details: String?) {
        val r = pending ?: return
        cleanup(); r.error(code, msg, details)
    }

    private fun cleanup() {
        pending = null
        listener?.let { apm.unregisterListener(it) }
        listener = null
        progress = null
        lastReport = null
    }

    companion object {
        val PACKS = listOf("models_asr", "models_llm")

        /** Play status -> the name the Dart download screen shows. */
        fun stateName(status: Int): String = when (status) {
            AssetPackStatus.DOWNLOADING -> "downloading"
            AssetPackStatus.TRANSFERRING, AssetPackStatus.COMPLETED -> "transferring"
            AssetPackStatus.WAITING_FOR_WIFI, AssetPackStatus.REQUIRES_USER_CONFIRMATION -> "waitingWifi"
            AssetPackStatus.FAILED -> "failed"
            else -> "pending"
        }

        fun reason(code: Int): String = when (code) {
            AssetPackErrorCode.NETWORK_ERROR -> "network"
            AssetPackErrorCode.INSUFFICIENT_STORAGE -> "storage"
            else -> code.toString()
        }
    }
}
