import 'package:fl_clash/common/branding_api.dart';
import 'package:fl_clash/models/models.dart';
import 'package:fl_clash/views/branding_login.dart';
import 'package:flutter_test/flutter_test.dart';

const _url = 'https://panel.example.com:8000/sub/abc';
const _account = BrandingAccount(username: 'alice', subscriptionUrl: _url);

void main() {
  group('applyBrandingAccount', () {
    test('selects the profile that already uses the subscription', () async {
      final existing = Profile.normal(label: 'old', url: _url);
      final other = Profile.normal(label: 'other', url: 'https://x.test/1');
      final selected = <int>[];
      var adds = 0;

      await applyBrandingAccount(
        account: _account,
        profiles: [other, existing],
        add: (url, {label}) async {
          adds++;
          return null;
        },
        select: selected.add,
      );

      expect(adds, 0);
      expect(selected, [existing.id]);
    });

    test('adds the subscription labelled with the account name', () async {
      final created = Profile.normal(label: 'alice', url: _url);
      final selected = <int>[];
      String? addedUrl;
      String? addedLabel;

      await applyBrandingAccount(
        account: _account,
        profiles: const [],
        add: (url, {label}) async {
          addedUrl = url;
          addedLabel = label;
          return created;
        },
        select: selected.add,
      );

      expect(addedUrl, _url);
      expect(addedLabel, 'alice');
      expect(selected, [created.id]);
    });

    test('selects nothing when the subscription cannot be added', () async {
      final selected = <int>[];

      await applyBrandingAccount(
        account: _account,
        profiles: const [],
        add: (url, {label}) async => null,
        select: selected.add,
      );

      expect(selected, isEmpty);
    });
  });
}
