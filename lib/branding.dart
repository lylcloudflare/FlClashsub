import 'package:fl_clash/common/common.dart';
import 'package:fl_clash/providers/providers.dart';
import 'package:fl_clash/widgets/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

const brandingSubscriptionUrl = '这里换成你的订阅链接';
const brandingUnlockCode = 'suks';

Future<void> addBrandingProfileIfEmpty(WidgetRef ref) async {
  if (!brandingSubscriptionUrl.startsWith('http')) return;
  if (ref.read(profilesProvider).isNotEmpty) return;
  await ref
      .read(profilesActionProvider.notifier)
      .addProfileFormURL(brandingSubscriptionUrl);
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
