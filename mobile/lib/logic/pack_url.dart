/// Which URLs the one-tap pack download may fetch (#1567): release assets of this repo only.
/// Mirrors PackDownloader.allowedStart on the Android side, which enforces it again.
const packReleasePrefix = 'https://github.com/sunososobro-hub/tinglongwu/releases/download/';

bool isAllowedPackUrl(String url) => url.startsWith(packReleasePrefix) && !url.contains('..');
