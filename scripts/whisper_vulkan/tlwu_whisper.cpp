/* #783: a flat C API over whisper.cpp for tinglongwu.asr_vulkan (ctypes).
 * whisper_full() takes `struct whisper_full_params` by value and its layout changes between whisper.cpp versions, so
 * Python never builds that struct: this file is compiled against the pinned whisper.h and only plain types cross.
 * C++ so no exception (std::bad_alloc in whisper_init_state, ...) crosses the C ABI into Python. */
#include <cstring>
#include "whisper.h"

#ifdef _WIN32
#define TLWU_API extern "C" __declspec(dllexport)
#else
#define TLWU_API extern "C" __attribute__((visibility("default")))
#endif

#define TLWU_WHISPER_ABI 1

TLWU_API int tlwu_whisper_abi(void) { return TLWU_WHISPER_ABI; }

TLWU_API void * tlwu_whisper_init(const char * model_path, int use_gpu) {
    struct whisper_context_params cp = whisper_context_default_params();
    cp.use_gpu = use_gpu != 0;
    try {
        return whisper_init_from_file_with_params(model_path, cp);
    } catch (...) {
        return nullptr;
    }
}

TLWU_API void tlwu_whisper_free(void * ctx) {
    if (ctx) whisper_free((struct whisper_context *) ctx);
}

/* 0 = ok (text may be empty), < 0 = whisper_full failed. lang: a code or "auto". lang_out gets the used code. */
TLWU_API int tlwu_whisper_transcribe(void * vctx, const float * pcm, int n_samples, const char * lang, int n_threads,
                                     char * text, int text_len, char * lang_out, int lang_out_len,
                                     float * no_speech, float * avg_logprob) {
    struct whisper_context * ctx = (struct whisper_context *) vctx;
    struct whisper_full_params p = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    p.print_progress = false;
    p.print_realtime = false;
    p.print_timestamps = false;
    p.print_special = false;
    p.no_timestamps = true;
    p.language = (lang && lang[0]) ? lang : "auto";
    if (n_threads > 0) p.n_threads = n_threads;
    if (text_len > 0) text[0] = '\0';
    if (lang_out_len > 0) lang_out[0] = '\0';
    *no_speech = 0.0f;
    *avg_logprob = 0.0f;
    try {
        if (whisper_full(ctx, p, pcm, n_samples) != 0) return -1;
    } catch (...) {
        return -2;
    }

    const whisper_token eot = whisper_token_eot(ctx);
    size_t used = 0;
    double logsum = 0.0;
    int ntok = 0;
    const int nseg = whisper_full_n_segments(ctx);
    for (int s = 0; s < nseg; ++s) {
        const char * seg = whisper_full_get_segment_text(ctx, s);
        size_t len = seg ? strlen(seg) : 0;
        if (text_len > 0 && used + len >= (size_t) text_len) len = (size_t) text_len - 1 - used;
        if (len > 0) { memcpy(text + used, seg, len); used += len; text[used] = '\0'; }
        const float ns = whisper_full_get_segment_no_speech_prob(ctx, s);
        if (s == 0 || ns > *no_speech) *no_speech = ns;
        for (int t = 0; t < whisper_full_n_tokens(ctx, s); ++t) {
            whisper_token_data d = whisper_full_get_token_data(ctx, s, t);
            if (d.id < eot) { logsum += d.plog; ++ntok; }  /* text tokens only, like faster-whisper's avg_logprob */
        }
    }
    if (ntok > 0) *avg_logprob = (float) (logsum / ntok);
    const char * code = whisper_lang_str(whisper_full_lang_id(ctx));
    if (code && lang_out_len > 0) { strncpy(lang_out, code, (size_t) lang_out_len - 1); lang_out[lang_out_len - 1] = '\0'; }
    return 0;
}
