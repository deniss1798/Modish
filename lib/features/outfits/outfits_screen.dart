import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';

class OutfitsScreen extends StatefulWidget {
  const OutfitsScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<OutfitsScreen> createState() => _OutfitsScreenState();
}

class _OutfitsScreenState extends State<OutfitsScreen> {
  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onCtrl);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onCtrl);
    super.dispose();
  }

  void _onCtrl() => setState(() {});

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final outfits = c.outfits;
    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(18, 22, 18, 20),
        children: [
          const Center(child: Brand()),
          const SizedBox(height: 12),
          const Center(
            child: Text('Образы', style: TextStyle(fontSize: 28, color: AppColors.ink)),
          ),
          const SizedBox(height: 12),
          SoftCard(
            child: Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: c.isLoading ? null : () => c.generateOutfitsV2(count: 3),
                    icon: const Icon(Icons.auto_awesome),
                    label: const Text('Сгенерировать 3'),
                  ),
                ),
                const SizedBox(width: 10),
                IconButton.filledTonal(
                  onPressed: c.isLoading ? null : () => c.refreshRemoteData(),
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          if (outfits.isEmpty)
            const SoftCard(
              child: Padding(
                padding: EdgeInsets.all(18),
                child: Text('Пока нет образов. Сгенерируйте первые 3.', style: TextStyle(color: AppColors.muted)),
              ),
            )
          else
            ...outfits.map((o) {
              final title = (o['style_direction'] ?? 'outfit').toString();
              final reason = (o['reason'] ?? '').toString();
              final total = (o['total_price'] ?? 0).toString();
              final saved = o['is_saved'] == true;
              return Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: SoftCard(
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(title, style: const TextStyle(fontFamily: 'Georgia', fontSize: 20)),
                    subtitle: Text('$reason\nИтого: $total'),
                    trailing: IconButton(
                      icon: Icon(saved ? Icons.bookmark : Icons.bookmark_border),
                      onPressed: c.isLoading || saved ? null : () => c.saveOutfit('${o['id']}'),
                    ),
                  ),
                ),
              );
            }),
        ],
      ),
    );
  }
}

