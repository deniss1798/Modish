import 'package:flutter/material.dart';
import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

class MobileViewport extends StatelessWidget {
  const MobileViewport({
    super.key,
    required this.child,
    this.minimalBackdrop = false,
  });
  final Widget child;
  final bool minimalBackdrop;

  @override
  Widget build(BuildContext context) {
    final content = Align(
      alignment: Alignment.topCenter,
      child: SizedBox(width: 390, child: child),
    );
    if (minimalBackdrop) {
      return Scaffold(backgroundColor: AppColors.bg, body: content);
    }
    return Scaffold(
      body: Stack(
        children: [
          const Positioned.fill(
            child: DecoratedBox(decoration: BoxDecoration(color: AppColors.bg)),
          ),
          content,
        ],
      ),
    );
  }
}

class Brand extends StatelessWidget {
  const Brand({super.key, this.width = 168, this.light = false, this.gold = true});
  final double width;
  final bool light;
  final bool gold;

  static const _logoPath = 'assets/images/logo.png';

  @override
  Widget build(BuildContext context) {
    if (gold) {
      return GoldWordmark(fontSize: width * 0.19);
    }
    return Image.asset(
      _logoPath,
      width: width,
      fit: BoxFit.contain,
      alignment: Alignment.centerLeft,
      errorBuilder: (context, error, stackTrace) => GoldWordmark(fontSize: width * 0.19),
    );
  }
}

class GoldWordmark extends StatelessWidget {
  const GoldWordmark({super.key, this.fontSize = 32});

  final double fontSize;

  @override
  Widget build(BuildContext context) {
    final base = TextStyle(
      fontFamily: 'Georgia',
      fontSize: fontSize,
      fontWeight: FontWeight.w500,
      height: 1.05,
      color: Colors.white,
    );
    return ShaderMask(
      blendMode: BlendMode.srcIn,
      shaderCallback: (bounds) => const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [AppColors.accentSoft, AppColors.accent],
      ).createShader(bounds),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Text('Mod', style: base),
          Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                '✦',
                style: base.copyWith(fontSize: fontSize * 0.28, height: 0.85, fontFamily: null),
              ),
              Text('i', style: base),
            ],
          ),
          Text('sh', style: base),
        ],
      ),
    );
  }
}

class SoftCard extends StatelessWidget {
  const SoftCard({super.key, required this.child, this.padding});
  final Widget child;
  final EdgeInsetsGeometry? padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: padding ?? const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.line),
        boxShadow: const [
          BoxShadow(
            color: Color(0x18000000),
            blurRadius: 10,
            offset: Offset(0, 3),
          ),
        ],
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
    this.expanded = true,
    this.backgroundColor,
    this.foregroundColor,
  });
  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;
  final bool expanded;
  final Color? backgroundColor;
  final Color? foregroundColor;

  @override
  Widget build(BuildContext context) {
    final bg = backgroundColor ?? AppColors.accent;
    final fg = foregroundColor ?? AppColors.onAccent;
    final btn = FilledButton(
      onPressed: onPressed,
      style: FilledButton.styleFrom(
        backgroundColor: bg,
        foregroundColor: fg,
        disabledBackgroundColor: AppColors.line,
        minimumSize: const Size(0, 50),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
      child: Row(
        mainAxisSize: expanded ? MainAxisSize.max : MainAxisSize.min,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          if (icon != null) ...[Icon(icon, size: 20), const SizedBox(width: 8)],
          Flexible(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );
    return expanded ? SizedBox(width: double.infinity, child: btn) : btn;
  }
}

class SecondaryButton extends StatelessWidget {
  const SecondaryButton({
    super.key,
    required this.label,
    this.onPressed,
    this.expanded = true,
    this.onDark = false,
    this.borderColor,
    this.foregroundColor,
  });
  final String label;
  final VoidCallback? onPressed;
  final bool expanded;
  final bool onDark;
  final Color? borderColor;
  final Color? foregroundColor;

  @override
  Widget build(BuildContext context) {
    final fg = foregroundColor ?? (onDark ? AppColors.accentSoft : AppColors.accent);
    final border = borderColor ?? (onDark ? AppColors.accent : AppColors.accentDark);
    final btn = OutlinedButton(
      onPressed: onPressed,
      style: OutlinedButton.styleFrom(
        foregroundColor: fg,
        side: BorderSide(color: border),
        minimumSize: const Size(0, 50),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
      child: Text(
        label,
        style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
      ),
    );
    return expanded ? SizedBox(width: double.infinity, child: btn) : btn;
  }
}

class SocialButton extends StatelessWidget {
  const SocialButton({
    super.key,
    required this.label,
    required this.icon,
    this.onPressed,
  });
  final String label;
  final IconData icon;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return OutlinedButton.icon(
      onPressed: onPressed,
      icon: Icon(icon, size: 20),
      label: Text(label),
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.ink,
        side: const BorderSide(color: AppColors.line),
        minimumSize: const Size(double.infinity, 46),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
    );
  }
}

class ModishChip extends StatelessWidget {
  const ModishChip({
    super.key,
    required this.label,
    required this.selected,
    required this.onTap,
  });
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: Material(
        color: selected ? AppColors.accent : AppColors.chipBg,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: BorderSide(color: selected ? AppColors.accent : AppColors.line),
        ),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(20),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
            child: Text(
              label,
              style: AppTextStyles.chip.copyWith(
                color: selected ? AppColors.onAccent : AppColors.ink,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class ScreenHeader extends StatelessWidget {
  const ScreenHeader({
    super.key,
    required this.title,
    this.subtitle,
    this.trailing,
    this.showBrand = false,
  });
  final String title;
  final String? subtitle;
  final Widget? trailing;
  final bool showBrand;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (showBrand) ...[const Brand(width: 120), const SizedBox(height: 12)],
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: AppTextStyles.screenTitle),
                    if (subtitle != null) ...[
                      const SizedBox(height: 4),
                      Text(subtitle!, style: AppTextStyles.bodyMuted),
                    ],
                  ],
                ),
              ),
              ?trailing,
            ],
          ),
        ],
      ),
    );
  }
}

class StatTile extends StatelessWidget {
  const StatTile({super.key, required this.value, required this.label});
  final String value;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        children: [
          Text(
            value,
            style: const TextStyle(
              fontSize: 17,
              fontWeight: FontWeight.w700,
              color: AppColors.ink,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            style: AppTextStyles.caption,
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

class ModishBottomNav extends StatelessWidget {
  const ModishBottomNav({
    super.key,
    required this.index,
    required this.onChanged,
  });
  final int index;
  final ValueChanged<int> onChanged;

  static const _items = [
    (Icons.home_outlined, Icons.home, 'Подборка'),
    (Icons.style_outlined, Icons.style, 'Образы'),
    (Icons.bookmark_border, Icons.bookmark, 'Сохраненное'),
    (Icons.person_outline, Icons.person, 'Профиль'),
  ];

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        color: AppColors.card,
        border: Border(top: BorderSide(color: AppColors.line)),
        boxShadow: [
          BoxShadow(
            color: Color(0x33000000),
            blurRadius: 12,
            offset: Offset(0, -4),
          ),
        ],
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 5),
          child: Row(
            children: List.generate(_items.length, (i) {
              final item = _items[i];
              final on = i == index;
              return Expanded(
                child: InkWell(
                  onTap: () => onChanged(i),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        on ? item.$2 : item.$1,
                        color: on ? AppColors.accent : AppColors.muted,
                        size: 22,
                      ),
                      const SizedBox(height: 4),
                      Text(
                        item.$3,
                        style: TextStyle(
                          fontSize: 10,
                          fontWeight: on ? FontWeight.w600 : FontWeight.w400,
                          color: on ? AppColors.accent : AppColors.muted,
                        ),
                      ),
                    ],
                  ),
                ),
              );
            }),
          ),
        ),
      ),
    );
  }
}

class HeroFashionBackdrop extends StatelessWidget {
  const HeroFashionBackdrop({super.key});

  static const _imagePath = 'assets/images/back.png';

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: [
        Image.asset(
          _imagePath,
          fit: BoxFit.cover,
          alignment: const Alignment(-0.45, 0),
          errorBuilder: (context, error, stackTrace) => const DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  Color(0xFF35312E),
                  Color(0xFF221F1D),
                  Color(0xFF121010),
                ],
                stops: [0.0, 0.5, 1.0],
              ),
            ),
          ),
        ),
        const DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [
                Color(0x52000000),
                Color(0x28000000),
                Color(0x5A000000),
                Color(0xE6000000),
              ],
              stops: [0.0, 0.28, 0.52, 1.0],
            ),
          ),
        ),
      ],
    );
  }
}

class ModishTextField extends StatelessWidget {
  const ModishTextField({
    super.key,
    required this.label,
    required this.controller,
    this.obscure = false,
    this.hint,
    this.icon,
  });
  final String label;
  final TextEditingController controller;
  final bool obscure;
  final String? hint;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTextStyles.caption.copyWith(
            fontWeight: FontWeight.w600,
            color: AppColors.ink,
          ),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: controller,
          obscureText: obscure,
          style: const TextStyle(color: AppColors.ink),
          cursorColor: AppColors.accent,
          decoration: InputDecoration(
            hintText: hint,
            prefixIcon: icon != null
                ? Icon(icon, color: AppColors.muted)
                : null,
            filled: true,
            fillColor: AppColors.card,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: AppColors.line),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: AppColors.line),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: AppColors.accent, width: 1.2),
            ),
          ),
        ),
      ],
    );
  }
}

class MenuTile extends StatelessWidget {
  const MenuTile({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.onTap,
    this.trailing,
  });
  final IconData icon;
  final String title;
  final String? subtitle;
  final VoidCallback? onTap;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Icon(icon, color: AppColors.accent),
      title: Text(title, style: const TextStyle(fontWeight: FontWeight.w500, color: AppColors.ink)),
      subtitle: subtitle != null
          ? Text(subtitle!, style: AppTextStyles.caption)
          : null,
      trailing:
          trailing ?? const Icon(Icons.chevron_right, color: AppColors.accent),
      onTap: onTap,
    );
  }
}
