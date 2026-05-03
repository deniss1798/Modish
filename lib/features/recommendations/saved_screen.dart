import 'package:flutter/material.dart';
import '../../app.dart';
import '../../core/theme/app_colors.dart';
import '../../core/widgets/modish_widgets.dart';
import 'models.dart';

enum _SavedFilter { all, daily, office, evening }

class SavedScreen extends StatefulWidget {
  const SavedScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<SavedScreen> createState() => _SavedScreenState();
}

class _SavedScreenState extends State<SavedScreen> {
  _SavedFilter _filter = _SavedFilter.all;

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

  bool _matches(Outfit o) {
    switch (_filter) {
      case _SavedFilter.all:
        return true;
      case _SavedFilter.daily:
        return o.occasionTags.contains('daily');
      case _SavedFilter.office:
        return o.occasionTags.contains('office');
      case _SavedFilter.evening:
        return o.occasionTags.contains('evening');
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final entries = <({String savedId, String recId, String savedAt, Outfit outfit})>[];
    for (final row in c.savedRows) {
      final rec = row['recommendation'];
      if (rec is! Map<String, dynamic>) continue;
      final outfit = Outfit.fromApi(rec);
      if (!_matches(outfit)) continue;
      entries.add((
        savedId: '${row['id']}',
        recId: '${row['recommendation_id']}',
        savedAt: '${row['saved_at'] ?? ''}',
        outfit: outfit,
      ));
    }

    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(18, 22, 18, 20),
        children: [
          const Center(child: Brand()),
          const SizedBox(height: 16),
          const Text(
            'Сохраненное',
            style: TextStyle(
              fontFamily: 'Georgia',
              fontSize: 40,
              color: AppColors.ink,
            ),
          ),
          const SizedBox(height: 12),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _chip('Все', _SavedFilter.all),
                _chip('Каждый день', _SavedFilter.daily),
                _chip('Офис', _SavedFilter.office),
                _chip('Вечер', _SavedFilter.evening),
              ],
            ),
          ),
          const SizedBox(height: 12),
          if (entries.isEmpty)
            const SoftCard(
              child: Center(
                child: Padding(
                  padding: EdgeInsets.all(28),
                  child: Text('Сохраненных образов пока нет'),
                ),
              ),
            )
          else
            ...entries.map(
              (e) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: SoftCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(
                          e.outfit.title,
                          style: const TextStyle(
                            fontSize: 22,
                            fontFamily: 'Georgia',
                          ),
                        ),
                        subtitle: Text(
                          '${e.outfit.description}\nСохранено: ${e.savedAt}',
                        ),
                        trailing: IconButton(
                          tooltip: 'Удалить из сохранённого',
                          icon: const Icon(Icons.bookmark_remove_outlined),
                          onPressed: c.isLoading
                              ? null
                              : () => c.sendFeedback(e.recId, 'unsave'),
                        ),
                        onTap: () => openDetails(context, e.outfit, c),
                      ),
                    ],
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _chip(String label, _SavedFilter value) {
    final selected = _filter == value;
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: FilterChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) => setState(() => _filter = value),
        selectedColor: AppColors.accent.withValues(alpha: .12),
      ),
    );
  }
}
