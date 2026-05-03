import 'package:flutter/material.dart';
import '../theme/app_colors.dart';

class MobileViewport extends StatelessWidget {
  const MobileViewport({
    super.key,
    required this.child,
    /// Без градиента и розовой подложки (онбординг / полноэкранные состояния).
    this.minimalBackdrop = false,
  });
  final Widget child;
  final bool minimalBackdrop;

  @override
  Widget build(BuildContext context) {
    final content = Align(
      alignment: Alignment.topCenter,
      child: SizedBox(width: 430, child: child),
    );
    if (minimalBackdrop) {
      return Scaffold(
        backgroundColor: AppColors.bg,
        body: content,
      );
    }
    return Scaffold(
      body: Stack(
        children: [
          Positioned.fill(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [AppColors.card, AppColors.bg, AppColors.bg],
                ),
              ),
            ),
          ),
          Positioned(
            left: -80,
            right: -80,
            bottom: 40,
            child: Transform.rotate(
              angle: -.1,
              child: Container(
                height: 170,
                color: const Color(0xFFF1E4E5).withValues(alpha: .6),
              ),
            ),
          ),
          content,
        ],
      ),
    );
  }
}

class Brand extends StatelessWidget {
  const Brand({super.key, this.size = 46});
  final double size;

  @override
  Widget build(BuildContext context) {
    return Text(
      'Modish',
      style: TextStyle(
        fontFamily: 'Georgia',
        fontSize: size,
        fontWeight: FontWeight.w500,
        color: AppColors.ink,
      ),
    );
  }
}

class SoftCard extends StatelessWidget {
  const SoftCard({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: AppColors.line),
      ),
      child: child,
    );
  }
}

class PrimaryButton extends StatelessWidget {
  const PrimaryButton({
    super.key,
    required this.label,
    this.icon,
    this.onPressed,
  });
  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return FilledButton.icon(
      onPressed: onPressed,
      icon: Icon(icon ?? Icons.arrow_forward),
      label: Padding(
        padding: const EdgeInsets.symmetric(vertical: 14),
        child: Text(
          label,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
        ),
      ),
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.accent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
    );
  }
}
