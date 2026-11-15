import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/conversation.dart';
import 'package:mobile/services/pipeline.dart';
import 'package:mobile/ui/subtitle_screen.dart';

void main() {
  group('Conversation routing (#558)', () {
    test('default is listen-only: picked target, nothing spoken', () {
      const c = Conversation();
      expect(c.enabled, isFalse);
      expect(
        c.route('en', listenTarget: 'zh'),
        const ConversationRoute(src: 'en', tgt: 'zh', speaker: Speaker.them, speak: false),
      );
      expect(c.route('zh', listenTarget: 'en').tgt, 'en');
      expect(c.targetFor('en'), 'en');
    });

    test('my side: zh -> English, spoken; their side: en -> zh, shown only', () {
      const on = Conversation(enabled: true, side: Speaker.me);
      expect(
        on.route('zh', listenTarget: 'zh'),
        const ConversationRoute(src: 'zh', tgt: 'en', speaker: Speaker.me, speak: true),
      );
      expect(on.route('yue', listenTarget: 'zh').tgt, 'en');
      expect(on.targetFor('zh'), 'en');

      final them = on.switched();
      expect(them.side, Speaker.them);
      expect(
        them.route('en', listenTarget: 'en'),
        const ConversationRoute(src: 'en', tgt: 'zh', speaker: Speaker.them, speak: false),
      );
      expect(them.targetFor('en'), 'zh');
      expect(them.switched().side, Speaker.me);
    });

    test('my line already in the foreign language is not spoken again', () {
      const on = Conversation(enabled: true, side: Speaker.me);
      expect(on.route('en', listenTarget: 'zh').speak, isFalse);
    });

    test('merge key separates directions', () {
      const on = Conversation(enabled: true, side: Speaker.me);
      expect(
        on.route('zh', listenTarget: 'zh').mergeKey,
        isNot(on.switched().route('zh', listenTarget: 'zh').mergeKey),
      );
    });

    test('unknown foreign language falls back to en (ru comes later)', () {
      expect(Conversation.normalizeForeign('ru'), 'en');
      expect(Conversation.normalizeForeign(null), 'en');
      expect(const Conversation().copyWith(foreign: 'xx').foreign, 'en');
    });

    test('format/parse round trip; bad input = defaults', () {
      const c = Conversation(enabled: true, side: Speaker.me);
      expect(Conversation.parse(c.format()), c);
      expect(Conversation.parse(null), const Conversation());
      expect(Conversation.parse('enabled=yes\nside=x\nforeign=zz'), const Conversation());
    });

    test('store remembers the mode; missing file = off', () {
      final dir = Directory.systemTemp.createTempSync('conv');
      try {
        final store = ConversationStore('${dir.path}/conversation.txt');
        expect(store.load(), const Conversation());
        store.save(const Conversation(enabled: true, side: Speaker.me));
        expect(
          ConversationStore('${dir.path}/conversation.txt').load(),
          const Conversation(enabled: true, side: Speaker.me),
        );
      } finally {
        dir.deleteSync(recursive: true);
      }
    });
  });

  group('Pipeline + screen (#558)', () {
    TestWidgetsFlutterBinding.ensureInitialized();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('com.llfbandit.record/messages'),
      (call) async => null,
    );

    Pipeline make() => Pipeline()..stage = Stage.listening;

    test('active target follows the mode and side; phone audio stays listen-only', () {
      final p = make();
      expect(p.activeTarget, 'zh');
      p.setConversationEnabled(true);
      expect(p.activeTarget, 'zh'); // default side: the other person talks -> zh
      p.switchSpeaker();
      expect(p.conversation.side, Speaker.me);
      expect(p.activeTarget, 'en');
      p.source = AudioSourceKind.phone;
      expect(p.activeTarget, 'zh');
      p.source = AudioSourceKind.mic;
      p.setConversationEnabled(false);
      expect(p.activeTarget, 'zh');
    });

    testWidgets('toggle turns 對話模式 on, side button switches, my bubble on the right', (t) async {
      final p = make();
      p.subtitles
        ..add(Subtitle('你好。', zh: 'Hi.', speaker: Speaker.me))
        ..add(Subtitle('OK.', zh: '好。'));
      await t.binding.setSurfaceSize(const Size(390, 844));
      await t.pumpWidget(
        MaterialApp(
          home: ListenableBuilder(
            listenable: p,
            builder: (_, _) => SubtitleScreen(pipeline: p),
          ),
        ),
      );
      expect(find.byKey(const Key('sideButton')), findsNothing);
      expect(find.text('自動辨識'), findsOneWidget);

      await t.tap(find.byKey(const Key('conversationToggle')));
      await t.pump();
      expect(p.conversation.enabled, isTrue);
      // No voice pack in the test: the pill says my lines are text only (#558 part 2).
      expect(find.text('對話模式  中文 ⇄ English  · 未安裝語音包，只顯示文字'), findsOneWidget);
      expect(find.text('對方在說（English）'), findsOneWidget);

      await t.tap(find.byKey(const Key('sideButton')));
      await t.pump();
      expect(p.conversation.side, Speaker.me);
      expect(find.text('我在說（中文）'), findsOneWidget);

      final mine = t.getCenter(find.text('Hi.'));
      final theirs = t.getCenter(find.text('好。'));
      expect(mine.dx, greaterThan(195));
      expect(theirs.dx, lessThan(195));
      await t.binding.setSurfaceSize(null);
    });

    testWidgets('phone audio source hides the conversation toggle', (t) async {
      final p = make()
        ..source = AudioSourceKind.phone
        ..conversation = const Conversation(enabled: true);
      await t.pumpWidget(MaterialApp(home: SubtitleScreen(pipeline: p)));
      expect(find.byKey(const Key('conversationToggle')), findsNothing);
      expect(find.byKey(const Key('sideButton')), findsNothing);
      expect(conversationActive(p), isFalse);
    });
  });
}
