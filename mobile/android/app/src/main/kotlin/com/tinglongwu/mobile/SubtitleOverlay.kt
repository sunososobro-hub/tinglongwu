package com.tinglongwu.mobile

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.provider.Settings
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Floating subtitle window above other apps (SYSTEM_ALERT_WINDOW, #998).
 * Drag the text to move it, drag the corner handle to resize the width, tap to return to the app.
 * The height follows the content (bilingual = source + translation, 3-4 lines) up to
 * [MAX_HEIGHT_FRACTION] of the screen; beyond that the oldest lines are cut at the top.
 * Hidden while the app itself is in front (its own subtitle screen shows the same lines).
 * All calls on the main thread.
 */
@SuppressLint("StaticFieldLeak") // holds the application context only
object SubtitleOverlay {
    private var app: Context? = null
    private var wm: WindowManager? = null
    private var root: LinearLayout? = null
    private var text: TextView? = null
    private var params: WindowManager.LayoutParams? = null

    private var wanted = false // Dart asked for the overlay (phone-audio mode)
    private var appInFront = true
    private var fontSp = 20f
    private var bgAlpha = 0.6f
    private var lastLines: List<Line> = emptyList()
    private var lastHint: String? = null
    private var lastDraft: String = ""
    private var lastDraftTr: String = ""
    private var clip: BottomClip? = null

    /** One subtitle: source text (shown smaller, above) and/or translation; null = not shown. */
    data class Line(val src: String?, val tr: String?)

    private const val MAX_HEIGHT_FRACTION = 0.40f
    private const val SOURCE_SCALE = 0.72f

    fun canDraw(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(ctx)

    fun show(ctx: Context) {
        app = ctx.applicationContext
        wanted = true
        sync()
    }

    fun hide() {
        wanted = false
        sync()
    }

    fun setAppInFront(front: Boolean) {
        appInFront = front
        sync()
    }

    fun update(lines: List<Line>, hint: String?, draft: String? = null, draftTr: String? = null) {
        lastLines = lines
        lastHint = hint
        lastDraft = draft?.trim().orEmpty()
        lastDraftTr = draftTr?.trim().orEmpty()
        render()
    }

    fun style(sp: Double?, alpha: Double?) {
        if (sp != null) fontSp = sp.toFloat().coerceIn(12f, 48f)
        if (alpha != null) bgAlpha = alpha.toFloat().coerceIn(0f, 1f)
        applyStyle()
    }

    private fun sync() {
        val ctx = app ?: return
        val visible = wanted && !appInFront && canDraw(ctx)
        if (visible && root == null) attach(ctx)
        if (!visible && root != null) detach()
    }

    private fun dp(ctx: Context, v: Float): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, ctx.resources.displayMetrics).roundToInt()

    @SuppressLint("ClickableViewAccessibility", "SetTextI18n")
    private fun attach(ctx: Context) {
        val w = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val screenW = ctx.resources.displayMetrics.widthPixels
        val type = if (Build.VERSION.SDK_INT >= 26) WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        else @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE
        val p = params ?: WindowManager.LayoutParams(
            (screenW * 0.92f).roundToInt(),
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = (screenW * 0.04f).roundToInt()
            y = (ctx.resources.displayMetrics.heightPixels * 0.70f).roundToInt()
        }
        params = p

        val tv = TextView(ctx).apply {
            setTextColor(Color.WHITE)
            setShadowLayer(4f, 0f, 1f, Color.BLACK)
            val pad = dp(ctx, 10f)
            setPadding(pad, dp(ctx, 6f), pad, dp(ctx, 6f))
        }
        val handle = TextView(ctx).apply {
            text = "⇲"
            setTextColor(Color.argb(160, 255, 255, 255))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            gravity = Gravity.BOTTOM
            setPadding(dp(ctx, 4f), 0, dp(ctx, 6f), dp(ctx, 2f))
        }
        val cl = BottomClip(ctx).apply {
            maxH = (ctx.resources.displayMetrics.heightPixels * MAX_HEIGHT_FRACTION).roundToInt()
            addView(tv, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT))
        }
        val box = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(cl, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(handle, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.MATCH_PARENT))
        }

        val slop = ViewConfiguration.get(ctx).scaledTouchSlop
        // Move the window, or open the app on a tap.
        var downX = 0f; var downY = 0f; var startX = 0; var startY = 0; var dragging = false
        tv.setOnTouchListener { _, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = e.rawX; downY = e.rawY; startX = p.x; startY = p.y; dragging = false
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = e.rawX - downX; val dy = e.rawY - downY
                    if (!dragging && (abs(dx) > slop || abs(dy) > slop)) dragging = true
                    if (dragging) {
                        p.x = startX + dx.roundToInt(); p.y = startY + dy.roundToInt()
                        w.updateViewLayout(box, p)
                    }
                }
                MotionEvent.ACTION_UP -> if (!dragging) openApp(ctx) else rememberFractions(lastW, lastH)
            }
            true
        }
        // Resize the width from the corner handle.
        var startW = 0
        handle.setOnTouchListener { _, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> { downX = e.rawX; startW = box.width }
                MotionEvent.ACTION_MOVE -> {
                    p.width = (startW + (e.rawX - downX)).roundToInt().coerceIn(dp(ctx, 160f), screenW)
                    w.updateViewLayout(box, p)
                }
                MotionEvent.ACTION_UP -> rememberFractions(lastW, lastH)
            }
            true
        }

        try {
            w.addView(box, p)
        } catch (e: Exception) {
            android.util.Log.e("tlwu.overlay", "addView failed", e)
            return
        }
        wm = w; root = box; text = tv; clip = cl
        val dm0 = ctx.resources.displayMetrics
        lastW = dm0.widthPixels; lastH = dm0.heightPixels
        rememberFractions(lastW, lastH)
        ctx.registerComponentCallbacks(configCb)
        applyStyle()
        render()
    }

    private fun detach() {
        try { wm?.removeView(root) } catch (_: Exception) {}
        try { app?.unregisterComponentCallbacks(configCb) } catch (_: Exception) {}
        root = null; text = null; wm = null; clip = null
    }

    // Rotation (#1078): the window position/width were in portrait pixels, so after landscape the
    // overlay sat below the screen. Keep them as screen fractions and refit on every size change.
    // Same algorithm as lib/logic/overlay_geometry.dart (fitOverlay), which has the unit tests.
    private var fx = 0.04f
    private var fy = 0.70f
    private var widthFrac = 0.92f
    private var lastW = 0
    private var lastH = 0

    private fun rememberFractions(sw: Int, sh: Int) {
        val p = params ?: return
        if (sw <= 0 || sh <= 0) return
        fx = p.x.toFloat() / sw; fy = p.y.toFloat() / sh; widthFrac = p.width.toFloat() / sw
    }

    private fun fitToScreen() {
        val ctx = app ?: return
        val p = params ?: return
        val r = root ?: return
        val dm = ctx.resources.displayMetrics
        val sw = dm.widthPixels; val sh = dm.heightPixels
        if (sw == lastW && sh == lastH) return
        lastW = sw; lastH = sh
        val minW = minOf(dp(ctx, 160f), sw)
        p.width = (sw * widthFrac).roundToInt().coerceIn(minW, sw)
        val h = minOf(r.height, sh)
        p.x = (sw * fx).roundToInt().coerceIn(0, maxOf(0, sw - p.width))
        p.y = (sh * fy).roundToInt().coerceIn(0, maxOf(0, sh - h))
        clip?.maxH = (sh * MAX_HEIGHT_FRACTION).roundToInt()
        try { wm?.updateViewLayout(r, p) } catch (_: Exception) {}
    }

    private val configCb = object : android.content.ComponentCallbacks {
        override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
            android.os.Handler(android.os.Looper.getMainLooper()).post { fitToScreen(); clip?.requestLayout() }
        }
        override fun onLowMemory() {}
    }

    private fun applyStyle() {
        val r = root ?: return
        val ctx = app ?: return
        text?.setTextSize(TypedValue.COMPLEX_UNIT_SP, fontSp)
        r.background = GradientDrawable().apply {
            cornerRadius = dp(ctx, 10f).toFloat()
            setColor(Color.argb((bgAlpha * 255).roundToInt(), 0, 0, 0))
        }
    }

    private fun render() {
        val tv = text ?: return
        val hint = lastHint
        if (hint != null) {
            tv.text = hint
            tv.setTextColor(Color.rgb(255, 200, 90))
        } else {
            tv.text = format(lastLines, lastDraft, lastDraftTr)
            tv.setTextColor(Color.WHITE)
        }
        clip?.requestLayout()
        fitToScreen()
    }

    /** Oldest first; each source line smaller and gray above its translation. */
    private fun format(lines: List<Line>, draft: String, draftTr: String): CharSequence {
        val b = SpannableStringBuilder()
        for (l in lines) {
            val src = l.src?.trim().orEmpty()
            val tr = l.tr?.trim().orEmpty()
            if (src.isNotEmpty()) {
                if (b.isNotEmpty()) b.append('\n')
                val start = b.length
                b.append(src)
                // Gray only when a translation follows (source-only mode stays white).
                if (tr.isNotEmpty()) {
                    b.setSpan(RelativeSizeSpan(SOURCE_SCALE), start, b.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    b.setSpan(ForegroundColorSpan(Color.rgb(200, 200, 200)), start, b.length,
                        Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                }
            }
            if (tr.isNotEmpty()) {
                if (b.isNotEmpty()) b.append('\n')
                b.append(tr)
            }
        }
        // Interim source text of the sentence still being spoken: small, gray, replaced by the
        // final line above once recognized (#1064).
        if (draft.isNotEmpty()) {
            if (b.isNotEmpty()) b.append('\n')
            val start = b.length
            b.append(draft)
            b.setSpan(RelativeSizeSpan(SOURCE_SCALE), start, b.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            b.setSpan(ForegroundColorSpan(Color.rgb(160, 160, 160)), start, b.length,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        // Chinese draft (#1065, optional): gray, below the source draft.
        if (draftTr.isNotEmpty()) {
            if (b.isNotEmpty()) b.append('\n')
            val start = b.length
            b.append(draftTr)
            b.setSpan(ForegroundColorSpan(Color.rgb(160, 160, 160)), start, b.length,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        return if (b.isEmpty()) "…" else b
    }

    /**
     * Wraps the text: height = content height up to [maxH]; when the content is taller, the
     * child is bottom-aligned so the newest lines stay visible and the oldest are clipped.
     */
    private class BottomClip(ctx: Context) : FrameLayout(ctx) {
        var maxH = Int.MAX_VALUE

        override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
            val c = getChildAt(0) ?: return super.onMeasure(widthMeasureSpec, heightMeasureSpec)
            val w = MeasureSpec.getSize(widthMeasureSpec)
            val cw = if (MeasureSpec.getMode(widthMeasureSpec) == MeasureSpec.UNSPECIFIED)
                MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED)
            else MeasureSpec.makeMeasureSpec(w, MeasureSpec.EXACTLY)
            c.measure(cw, MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED))
            setMeasuredDimension(
                if (MeasureSpec.getMode(widthMeasureSpec) == MeasureSpec.UNSPECIFIED) c.measuredWidth else w,
                minOf(c.measuredHeight, maxH))
        }

        override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) {
            val c = getChildAt(0) ?: return
            val h = b - t
            c.layout(0, h - c.measuredHeight, r - l, h)
        }
    }

    private fun openApp(ctx: Context) {
        ctx.startActivity(Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
    }
}
