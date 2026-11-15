/// zh-TW quotation marks (pure logic): the model emits curly Western quotes; Taiwan text uses
/// corner brackets.
library;

/// “ ” -> 「 」 and ‘ ’ -> 『 』. A closing ’ is converted only when a ‘ is open, so an English
/// apostrophe left in the text (don’t) is kept.
String toTwQuotes(String s) {
  if (!s.contains(RegExp('[“”‘’]'))) return s;
  final out = StringBuffer();
  var open = 0;
  for (final r in s.runes) {
    switch (String.fromCharCode(r)) {
      case '“':
        out.write('「');
      case '”':
        out.write('」');
      case '‘':
        open++;
        out.write('『');
      case '’':
        if (open > 0) {
          open--;
          out.write('』');
        } else {
          out.write('’');
        }
      default:
        out.writeCharCode(r);
    }
  }
  return out.toString();
}
