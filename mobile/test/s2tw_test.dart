import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/s2tw.dart';
import 'package:mobile/logic/zh_check.dart';

void main() {
  group('toTraditionalTw', () {
    // Expected values are the desktop output: tinglongwu.translate.zh_convert(text, "tw").
    const cases = <String, String>{
      '这是一个软件的头发问题': '這是一個軟體的頭髮問題',
      '我们通过网络连接两个服务器': '我們通過網路連接兩個伺服器',
      '他被发现在里面': '他被發現在裡面',
      '我支持你的决定': '我支持你的決定',
      '音乐家具有天赋': '音樂家具有天賦',
      '妳好，干净的面条': '你好，乾淨的麵條',
      '后来他去了台湾': '後來他去了臺灣',
      '我们的软件不支持这个': '我們的軟體不支持這個',
      '总统发表了讲话，表示经济正在复苏。': '總統發表了講話，表示經濟正在復甦。',
      '他们在内存里存储了程序': '他們在記憶體裡儲存了程式',
      // Phrases equal to the char-by-char result still set match boundaries (一台 keeps 台风 from matching).
      '一台风扇': '一臺風扇',
      '麦克风采集': '麥克風採集',
    };

    for (final e in cases.entries) {
      test('${e.key} -> ${e.value}', () {
        expect(isTraditionalChinese(e.key), isFalse);
        final out = toTraditionalTw(e.key);
        expect(out, e.value);
        expect(isTraditionalChinese(out), isTrue);
      });
    }

    test('Traditional text, Latin, digits and empty input are unchanged', () {
      expect(toTraditionalTw('已經是繁體中文了'), '已經是繁體中文了');
      expect(toTraditionalTw('GPU 2026 ok'), 'GPU 2026 ok');
      expect(toTraditionalTw(''), '');
    });

    test('characters outside the BMP pass through intact', () {
      expect(toTraditionalTw('𠀀这'), '𠀀這');
    });
  });
}
