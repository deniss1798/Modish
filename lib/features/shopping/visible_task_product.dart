import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'shopping_task_controller.dart';

/// A prefetched or offscreen card is not a brand exposure.
class VisibleTaskProduct extends StatefulWidget {
  const VisibleTaskProduct({
    super.key,
    required this.task,
    required this.productId,
    required this.child,
    this.outfitId,
  });
  final ShoppingTaskController task;
  final String productId;
  final String? outfitId;
  final Widget child;
  @override
  State<VisibleTaskProduct> createState() => _VisibleTaskProductState();
}

class _VisibleTaskProductState extends State<VisibleTaskProduct> {
  ScrollPosition? _position;
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _position?.removeListener(_schedule);
    _position = Scrollable.maybeOf(context)?.position;
    _position?.addListener(_schedule);
    _schedule();
  }

  void _schedule() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !(ModalRoute.of(context)?.isCurrent ?? true)) return;
      final box = context.findRenderObject();
      if (box is! RenderBox || !box.hasSize) return;
      final viewport = RenderAbstractViewport.maybeOf(box);
      if (viewport == null || viewport is! RenderBox) return;
      final viewportBox = viewport as RenderBox;
      if (!viewportBox.hasSize) return;
      final rect = box.localToGlobal(Offset.zero) & box.size;
      final view = viewportBox.localToGlobal(Offset.zero) & viewportBox.size;
      final visible = rect.intersect(view);
      if (!visible.isEmpty &&
          visible.width * visible.height >= rect.width * rect.height * .5) {
        unawaited(
          widget.task.recordVisibleProduct(
            widget.productId,
            outfitId: widget.outfitId,
          ),
        );
      }
    });
  }

  @override
  void dispose() {
    _position?.removeListener(_schedule);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
