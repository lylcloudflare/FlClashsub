import 'dart:io';

import 'package:dio/dio.dart';
import 'package:fl_clash/common/branding_api.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers/fake_http_adapter.dart';

const _base = 'https://auth.example.com:9000';
const _subscription = 'https://panel.example.com:8000/sub/abc';

BrandingApi _api(FakeHttpAdapter adapter, {String base = _base}) {
  final dio = newBrandingDio()..httpClientAdapter = adapter;
  return BrandingApi(dio: dio, base: base);
}

Map<String, Object?> _ok() {
  return {'ok': true, 'username': 'alice', 'subscription_url': _subscription};
}

Map<String, Object?> _fail(String code) {
  return {'ok': false, 'error': code, 'message': 'x'};
}

Future<BrandingApiException> _failure(Future<Object?> call) async {
  try {
    await call;
  } on BrandingApiException catch (e) {
    return e;
  }
  throw TestFailure('expected a BrandingApiException');
}

void main() {
  group('BrandingApi', () {
    test('login posts the credentials and returns the subscription', () async {
      final adapter = FakeHttpAdapter((_) => jsonResponse(_ok()));

      final account = await _api(adapter).login('alice', 'secret-pass');

      expect(account.username, 'alice');
      expect(account.subscriptionUrl, _subscription);
      final request = adapter.requests.single;
      expect(request.uri.toString(), '$_base/api/login');
      expect(request.method, 'POST');
      const expected = {'username': 'alice', 'password': 'secret-pass'};
      expect(request.data, expected);
    });

    test('register also sends the invite code', () async {
      final adapter = FakeHttpAdapter((_) => jsonResponse(_ok()));

      await _api(adapter).register('alice', 'secret-pass', 'ABCDE-FGHJK');

      final request = adapter.requests.single;
      expect(request.uri.toString(), '$_base/api/register');
      const expected = {
        'username': 'alice',
        'password': 'secret-pass',
        'invite': 'ABCDE-FGHJK',
      };
      expect(request.data, expected);
    });

    test('a trailing slash on the base does not double up', () async {
      final adapter = FakeHttpAdapter((_) => jsonResponse(_ok()));

      await _api(adapter, base: '$_base//').login('alice', 'secret-pass');

      expect(adapter.requests.single.uri.toString(), '$_base/api/login');
    });

    test('a server error code becomes a readable message', () async {
      final adapter = FakeHttpAdapter(
        (_) => jsonResponse(_fail('bad_credentials'), status: 401),
      );

      final error = await _failure(_api(adapter).login('alice', 'nope'));

      expect(error.code, 'bad_credentials');
      expect(error.message, '账号或密码错误');
    });

    test('an unknown error code falls back to the generic message', () async {
      final adapter = FakeHttpAdapter(
        (_) => jsonResponse(_fail('something_new'), status: 400),
      );

      final error = await _failure(_api(adapter).login('alice', 'nope'));

      expect(error.message, '登录失败，请稍后再试');
    });

    test('a body that is not json is reported as unknown', () async {
      final adapter = FakeHttpAdapter(
        (_) => ResponseBody.fromString('<html>bad gateway</html>', 502),
      );

      final error = await _failure(_api(adapter).login('alice', 'x'));

      expect(error.code, 'bad_response');
    });

    test('a subscription that is not a web address is rejected', () async {
      final adapter = FakeHttpAdapter(
        (_) => jsonResponse({..._ok(), 'subscription_url': 'file:///etc'}),
      );

      final error = await _failure(_api(adapter).login('alice', 'x'));

      expect(error.code, 'bad_response');
    });

    test('the account status is kept and explained when not active', () async {
      final adapter = FakeHttpAdapter(
        (_) => jsonResponse({..._ok(), 'status': 'expired'}),
      );

      final account = await _api(adapter).login('alice', 'x');

      expect(account.status, 'expired');
      expect(account.notice, '账号已到期，请联系管理员续期');
    });

    test('an active or missing status needs no notice', () async {
      final active = FakeHttpAdapter(
        (_) => jsonResponse({..._ok(), 'status': 'active'}),
      );
      final missing = FakeHttpAdapter((_) => jsonResponse(_ok()));

      expect((await _api(active).login('a', 'x')).notice, isNull);
      expect((await _api(missing).login('a', 'x')).notice, isNull);
    });

    test('limited and disabled accounts each get their own notice', () {
      const limited = BrandingAccount(
        username: 'a',
        subscriptionUrl: _subscription,
        status: 'limited',
      );
      const disabled = BrandingAccount(
        username: 'a',
        subscriptionUrl: _subscription,
        status: 'disabled',
      );

      expect(limited.notice, '流量已用完，请联系管理员');
      expect(disabled.notice, '账号已被停用，请联系管理员');
    });

    test('a certificate failure is reported as a tls error', () async {
      final adapter = FakeHttpAdapter((options) {
        throw DioException(
          requestOptions: options,
          error: const HandshakeException('CERTIFICATE_VERIFY_FAILED'),
        );
      });

      final error = await _failure(_api(adapter).login('alice', 'x'));

      expect(error.code, 'tls');
      expect(error.message, '服务器证书无效或已过期，请联系管理员');
    });

    test('a rejected certificate is reported as a tls error', () async {
      final adapter = FakeHttpAdapter((options) {
        throw DioException.badCertificate(requestOptions: options);
      });

      final error = await _failure(_api(adapter).login('alice', 'x'));

      expect(error.code, 'tls');
    });

    for (final type in [
      DioExceptionType.connectionTimeout,
      DioExceptionType.sendTimeout,
      DioExceptionType.receiveTimeout,
    ]) {
      test('a $type is reported as a timeout', () async {
        final adapter = FakeHttpAdapter((options) {
          throw DioException(requestOptions: options, type: type);
        });

        final error = await _failure(_api(adapter).login('alice', 'x'));

        expect(error.code, 'timeout');
        expect(error.message, '连接服务器超时，请稍后再试');
      });
    }

    test('a connection failure is reported as a network error', () async {
      final adapter = FakeHttpAdapter((options) {
        throw DioException.connectionError(
          requestOptions: options,
          reason: 'offline',
        );
      });

      final error = await _failure(_api(adapter).login('alice', 'x'));

      expect(error.code, 'network');
      expect(error.message, '无法连接服务器，请检查网络后重试');
    });
  });
}
