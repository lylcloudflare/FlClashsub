import 'dart:convert';
import 'dart:io' show TlsException;

import 'package:dio/dio.dart';
import 'package:fl_clash/branding_secret.dart';

const _errorMessages = {
  'bad_credentials': '账号或密码错误',
  'invalid_invite': '邀请码无效、已用完或已过期',
  'username_taken': '这个账号已被使用，换一个试试',
  'bad_username': '账号需为 3-24 位字母、数字或下划线',
  'bad_password': '密码需为 8-64 位',
  'too_many_attempts': '输错次数太多，请 15 分钟后再试',
  'too_many_requests': '操作太频繁，请稍后再试',
  'upstream_error': '服务暂时不可用，请稍后再试',
  'account_unlinked': '这个账号没有可用的订阅，请联系管理员',
  'network': '无法连接服务器，请检查网络后重试',
  'timeout': '连接服务器超时，请稍后再试',
  'tls': '服务器证书无效或已过期，请联系管理员',
  'bad_response': '登录服务返回了无法识别的内容，请确认服务地址是否正确',
  'unknown': '登录失败，请稍后再试',
};

const _statusNotices = {
  'expired': '账号已到期，请联系管理员续期',
  'limited': '流量已用完，请联系管理员',
  'disabled': '账号已被停用，请联系管理员',
};

bool get brandingLoginEnabled => brandingSecretApiBase.isNotEmpty;

class BrandingAccount {
  const BrandingAccount({
    required this.username,
    required this.subscriptionUrl,
    this.status = 'active',
  });

  final String username;
  final String subscriptionUrl;
  final String status;

  String? get notice => _statusNotices[status];
}

class BrandingApiException implements Exception {
  const BrandingApiException(this.code);

  final String code;

  String get message => _errorMessages[code] ?? _errorMessages['unknown']!;

  @override
  String toString() => 'BrandingApiException($code)';
}

Dio newBrandingDio() {
  final options = BaseOptions(
    connectTimeout: const Duration(seconds: 15),
    receiveTimeout: const Duration(seconds: 20),
    sendTimeout: const Duration(seconds: 20),
    responseType: ResponseType.plain,
    validateStatus: (_) => true,
  );
  return Dio(options);
}

final _defaultDio = newBrandingDio();

class BrandingApi {
  BrandingApi({this.dio, this.base = brandingSecretApiBase});

  final Dio? dio;
  final String base;

  String get _root => base.replaceFirst(RegExp(r'/+$'), '');

  Future<BrandingAccount> login(String username, String password) {
    final payload = {'username': username, 'password': password};
    return _post('/api/login', payload);
  }

  Future<BrandingAccount> register(
    String username,
    String password,
    String invite,
  ) {
    final payload = {
      'username': username,
      'password': password,
      'invite': invite,
    };
    return _post('/api/register', payload);
  }

  Future<BrandingAccount> _post(String path, Map<String, String> data) async {
    final client = dio ?? _defaultDio;
    final Response<String> response;
    try {
      response = await client.post<String>('$_root$path', data: data);
    } on DioException catch (e) {
      throw BrandingApiException(_networkCode(e));
    }
    return _parse(response.data);
  }

  String _networkCode(DioException e) {
    if (e.type == DioExceptionType.badCertificate || e.error is TlsException) {
      return 'tls';
    }
    return switch (e.type) {
      DioExceptionType.connectionTimeout ||
      DioExceptionType.sendTimeout ||
      DioExceptionType.receiveTimeout => 'timeout',
      _ => 'network',
    };
  }

  BrandingAccount _parse(String? raw) {
    final Object? json;
    try {
      json = jsonDecode(raw ?? '');
    } on FormatException {
      throw const BrandingApiException('bad_response');
    }
    if (json is! Map<String, dynamic>) {
      throw const BrandingApiException('bad_response');
    }
    if (json['ok'] != true) {
      final code = json['error'];
      throw BrandingApiException(code is String ? code : 'unknown');
    }
    final name = json['username'];
    final url = json['subscription_url'];
    if (name is! String || url is! String || !url.startsWith('http')) {
      throw const BrandingApiException('bad_response');
    }
    final status = json['status'];
    return BrandingAccount(
      username: name,
      subscriptionUrl: url,
      status: status is String ? status : 'active',
    );
  }
}
