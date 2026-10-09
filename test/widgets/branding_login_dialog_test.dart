import 'package:fl_clash/common/branding_api.dart';
import 'package:fl_clash/providers/app.dart';
import 'package:fl_clash/state.dart';
import 'package:fl_clash/views/branding_login.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';

import '../helpers/fake_http_adapter.dart';
import '../helpers/test_app.dart';

const _subscription = 'https://panel.example.com:8000/sub/abc';

class _Outcome {
  BrandingAccount? account;
  bool closed = false;
}

const _okReply = <String, Object?>{
  'ok': true,
  'username': 'alice',
  'subscription_url': _subscription,
};

FakeHttpAdapter _adapter({Map<String, Object?>? reply, int status = 200}) {
  final body = reply ?? _okReply;
  return FakeHttpAdapter((_) => jsonResponse(body, status: status));
}

Future<_Outcome> _open(WidgetTester tester, FakeHttpAdapter adapter) async {
  tester.view.physicalSize = const Size(1000, 900);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  final container = ProviderContainer();
  addTearDown(container.dispose);
  globalState.container = container;
  container
      .read(viewSizeProvider.notifier)
      .update((_) => const Size(1000, 900));

  final api = BrandingApi(
    dio: newBrandingDio()..httpClientAdapter = adapter,
    base: 'https://auth.example.com:9000',
  );
  final outcome = _Outcome();
  Future<void> open(BuildContext context) async {
    outcome.account = await showDialog<BrandingAccount>(
      context: context,
      builder: (_) => BrandingLoginDialog(api: api),
    );
    outcome.closed = true;
  }

  await tester.pumpWidget(
    UncontrolledProviderScope(
      container: container,
      child: TestApp(
        child: Builder(
          builder: (context) {
            return TextButton(
              onPressed: () => open(context),
              child: const Text('open'),
            );
          },
        ),
      ),
    ),
  );
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
  return outcome;
}

Future<void> _type(WidgetTester tester, int field, String text) async {
  await tester.enterText(find.byType(TextFormField).at(field), text);
  await tester.pump();
}

Future<void> _submit(WidgetTester tester) async {
  await tester.tap(find.text('Submit'));
  for (var i = 0; i < 5; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('login shows only account and password fields', (tester) async {
    await _open(tester, _adapter());

    expect(find.byType(TextFormField), findsNWidgets(2));
    expect(find.text('邀请码'), findsNothing);
    expect(find.text('没有账号？用邀请码注册'), findsOneWidget);
  });

  testWidgets('empty fields are rejected without calling the server', (
    tester,
  ) async {
    final adapter = _adapter();
    await _open(tester, adapter);

    await _submit(tester);

    expect(find.text('请输入账号'), findsOneWidget);
    expect(find.text('请输入密码'), findsOneWidget);
    expect(adapter.requests, isEmpty);
  });

  testWidgets('a successful login closes the dialog with the account', (
    tester,
  ) async {
    final adapter = _adapter();
    final outcome = await _open(tester, adapter);

    await _type(tester, 0, ' alice ');
    await _type(tester, 1, 'secret-pass');
    await _submit(tester);

    expect(outcome.closed, isTrue);
    expect(outcome.account?.subscriptionUrl, _subscription);
    const expected = {'username': 'alice', 'password': 'secret-pass'};
    expect(adapter.requests.single.data, expected);
  });

  testWidgets('a wrong password shows the message and keeps the dialog', (
    tester,
  ) async {
    final adapter = _adapter(
      reply: {'ok': false, 'error': 'bad_credentials', 'message': 'x'},
      status: 401,
    );
    final outcome = await _open(tester, adapter);

    await _type(tester, 0, 'alice');
    await _type(tester, 1, 'wrong-pass');
    await _submit(tester);

    expect(find.text('账号或密码错误'), findsOneWidget);
    expect(outcome.closed, isFalse);
    expect(find.byType(LinearProgressIndicator), findsNothing);
  });

  testWidgets('register asks for an invite code and checks the password', (
    tester,
  ) async {
    final adapter = _adapter();
    final outcome = await _open(tester, adapter);

    await tester.tap(find.text('没有账号？用邀请码注册'));
    await tester.pump();
    expect(find.byType(TextFormField), findsNWidgets(3));

    await _type(tester, 0, 'alice');
    await _type(tester, 1, 'short');
    await _type(tester, 2, 'ABCDE-FGHJK');
    await _submit(tester);
    expect(find.text('密码需为 8-64 位'), findsOneWidget);
    expect(adapter.requests, isEmpty);

    await _type(tester, 1, 'long-enough-pass');
    await _submit(tester);

    expect(outcome.closed, isTrue);
    expect(adapter.requests.single.uri.path, '/api/register');
    const expected = {
      'username': 'alice',
      'password': 'long-enough-pass',
      'invite': 'ABCDE-FGHJK',
    };
    expect(adapter.requests.single.data, expected);
  });

  testWidgets('cancel closes the dialog without an account', (tester) async {
    final outcome = await _open(tester, _adapter());

    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();

    expect(outcome.closed, isTrue);
    expect(outcome.account, isNull);
  });
}
