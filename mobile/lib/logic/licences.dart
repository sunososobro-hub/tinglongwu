/// Open-source licences shown in the app (#1347). Texts live in `assets/licenses/` so they read offline.
class LicenceEntry {
  const LicenceEntry(this.name, this.licence, this.credit, this.asset);
  final String name;
  final String licence;
  final String credit;
  final String asset;
}

const licenceEntries = <LicenceEntry>[
  LicenceEntry('SenseVoice', 'FunASR Model Open Source License 1.1 (attribution required)',
      'FunAudioLLM / Alibaba Group', 'assets/licenses/funasr-sensevoice.txt'),
  LicenceEntry('Silero VAD', 'MIT', 'Silero Team', 'assets/licenses/mit.txt'),
  LicenceEntry('Hy-MT2 1.8B', 'Apache-2.0', 'Copyright (C) 2026 Tencent', 'assets/licenses/apache-2.0.txt'),
  LicenceEntry('sherpa-onnx', 'Apache-2.0', 'k2-fsa authors', 'assets/licenses/apache-2.0.txt'),
  LicenceEntry('llama.cpp', 'MIT', 'ggml authors', 'assets/licenses/mit.txt'),
  LicenceEntry('OpenCC-derived data (s2tw)', 'Apache-2.0', 'BYVoid and contributors', 'assets/licenses/apache-2.0.txt'),
  LicenceEntry('Optional pack: English voice (vits-ljs)', 'Apache-2.0; LJSpeech public domain; CMU dictionary BSD-style',
      'k2-fsa / Keith Ito', 'assets/licenses/apache-2.0.txt'),
  LicenceEntry('Optional pack: Russian speech recognition (Vosk ru zipformer)', 'Apache-2.0',
      'Alpha Cephei / k2-fsa', 'assets/licenses/apache-2.0.txt'),
  LicenceEntry('Optional pack: Russian voice (Supertonic 3)', 'OpenRAIL-M (use restrictions apply); sample code MIT',
      'Supertone Inc.', 'assets/licenses/openrail-m-supertonic.txt'),
  LicenceEntry('Optional: speaker model (3D-Speaker CAM++)', 'Apache-2.0', 'Alibaba 3D-Speaker project',
      'assets/licenses/apache-2.0.txt'),
];
