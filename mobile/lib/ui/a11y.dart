import 'package:flutter/material.dart';

/// TalkBack / VoiceOver helpers for the listen screen (#1952, accessibility-listen-speak.md §2).

/// Spoken label of the round start / stop button.
String micLabel({required bool stopped}) => stopped ? '開始聆聽' : '停止聆聽';

/// The round mic button as a real button: label, enabled state and the tap action.
class MicSemantics extends StatelessWidget {
  const MicSemantics({super.key, required this.stopped, required this.onTap, required this.child});
  final bool stopped;
  final VoidCallback? onTap;
  final Widget child;

  @override
  Widget build(BuildContext context) => Semantics(
        button: true,
        enabled: onTap != null,
        label: micLabel(stopped: stopped),
        onTap: onTap,
        excludeSemantics: true,
        child: child,
      );
}

/// Content that changes while the user listens (listening / stopped, download progress, new
/// subtitle lines): a live region, so a screen reader speaks each change without moving focus.
class LiveRegion extends StatelessWidget {
  const LiveRegion({super.key, required this.child, this.label});
  final Widget child;
  final String? label;

  @override
  Widget build(BuildContext context) => Semantics(
        liveRegion: true,
        container: true,
        label: label,
        child: child,
      );
}

/// A named, selectable group (the subtitle mode choice): the screen reader says what it is.
class NamedGroup extends StatelessWidget {
  const NamedGroup({super.key, required this.label, required this.child});
  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) =>
      Semantics(container: true, label: label, child: child);
}
