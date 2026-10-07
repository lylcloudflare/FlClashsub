import 'package:fl_clash/common/common.dart';
import 'package:fl_clash/providers/providers.dart';
import 'package:fl_clash/widgets/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

const brandingSubscriptionUrls = <String>[
  'https://sossub.20030413.xyz/profiles/cf',
  'https://sossub.20030413.xyz/profiles/lr',
  'https://panel.0611520.xyz:8000/sub/bHI5OTksMTc5MTM0NjQ2MgSZeAd2Cgga',
];
const brandingUnlockCode = 'suks';

Future<void> addBrandingProfilesIfMissing(WidgetRef ref) async {
  for (final url in brandingSubscriptionUrls) {
    if (!url.startsWith('http')) continue;
    if (ref.read(profilesProvider).any((profile) => profile.url == url)) {
      continue;
    }
    await ref.read(profilesActionProvider.notifier).addProfileFormURL(url);
  }
}

Future<bool> askBrandingUnlockCode() async {
  final input = await dialogs.showCommonDialog<String>(
    child: InputDialog(
      title: currentAppLocalizations.addProfile,
      value: '',
      obscureText: true,
    ),
  );
  return input?.trim().toLowerCase() == brandingUnlockCode;
}
