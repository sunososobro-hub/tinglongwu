/// Whether the subtitle screen shows the grey debug stats line (#1118).
/// Hidden by default in release builds; shown in debug/profile builds, or when
/// the app is built with `--dart-define=TLW_DEBUG_STATS=true`.
const bool kDebugStatsDefine = bool.fromEnvironment('TLW_DEBUG_STATS');

bool showDebugStats({required bool releaseMode, bool forced = kDebugStatsDefine}) =>
    forced || !releaseMode;
