from pathlib import Path
p=Path('lib/core/widgets/product_image.dart');s=p.read_text(encoding='utf-8');start=s.index('      child: InkWell(',s.index('  Widget _placeholder'));end=s.index('\n    );\n  }\n}',start);s=s[:start]+'''      child: Stack(children: [
        Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 32, color: AppColors.muted),
          const SizedBox(height: 6),
          const Text('Фото недоступно', textAlign: TextAlign.center, style: TextStyle(fontSize: 10, color: AppColors.muted)),
        ])),
        Positioned(bottom: 0, right: 0, child: TextButton(
          onPressed: () {
            setState(() { _index = 0; _bytes = null; _decodeAdvanceScheduled = false; });
            _kickLoad();
          },
          child: const Text('Повторить', style: TextStyle(fontSize: 10)),
        )),
      ]),''' +s[end:];p.write_text(s,encoding='utf-8')
# Remove formatting-only changes in untouched files.
import subprocess
for path in ['lib/features/products/outfit_slots.dart','lib/features/products/pairing.dart']:
 Path(path).write_bytes(subprocess.check_output(['git','show','HEAD:'+path]))
