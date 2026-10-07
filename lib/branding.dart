import 'package:fl_clash/branding_secret.dart';
import 'package:fl_clash/common/common.dart';
import 'package:fl_clash/providers/providers.dart';
import 'package:fl_clash/widgets/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

Future<void> addBrandingProfilesIfMissing(WidgetRef ref) async {
  for (final url in brandingSecretUrls) {
    if (!url.startsWith('http')) continue;
    if (ref.read(profilesProvider).any((profile) => profile.url == url)) {
      continue;
    }
    await ref.read(profilesActionProvider.notifier).addProfileFormURL(url);
  }
}

Future<bool> askBrandingUnlockCode() async {
  if (brandingSecretCode.isEmpty) return false;
  final input = await dialogs.showCommonDialog<String>(
    child: InputDialog(
      title: currentAppLocalizations.addProfile,
      value: '',
      obscureText: true,
    ),
  );
  return input?.trim().toLowerCase() == brandingSecretCode.toLowerCase();
}
