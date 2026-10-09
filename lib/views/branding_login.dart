import 'package:fl_clash/common/branding.dart';
import 'package:fl_clash/common/branding_api.dart';
import 'package:fl_clash/common/common.dart';
import 'package:fl_clash/providers/providers.dart';
import 'package:fl_clash/state.dart';
import 'package:fl_clash/widgets/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:material_ui/material_ui.dart';

const brandingLoginLabel = '登录账号';

Future<void> setupBranding(WidgetRef ref) async {
  await addBrandingProfilesIfMissing(ref);
  if (!brandingLoginEnabled) return;
  if (ref.read(profilesProvider).isNotEmpty) return;
  await showBrandingLogin();
}

Future<void> showBrandingLogin() async {
  final account = await dialogs.showCommonDialog<BrandingAccount>(
    child: const BrandingLoginDialog(),
  );
  if (account == null) return;
  final action = globalState.container.read(profilesActionProvider.notifier);
  await action.addProfileFormURL(account.subscriptionUrl);
}

class BrandingLoginDialog extends StatefulWidget {
  const BrandingLoginDialog({super.key, this.api});

  final BrandingApi? api;

  @override
  State<BrandingLoginDialog> createState() => _BrandingLoginDialogState();
}

class _BrandingLoginDialogState extends State<BrandingLoginDialog> {
  static final _nameRule = RegExp(r'^[A-Za-z0-9_]{3,24}$');

  final _formKey = GlobalKey<FormState>();
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _invite = TextEditingController();
  bool _register = false;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _username.dispose();
    _password.dispose();
    _invite.dispose();
    super.dispose();
  }

  String? _checkName(String? value) {
    final text = (value ?? '').trim();
    if (text.isEmpty) return '请输入账号';
    if (_register && !_nameRule.hasMatch(text)) {
      return '账号需为 3-24 位字母、数字或下划线';
    }
    return null;
  }

  String? _checkPassword(String? value) {
    final text = value ?? '';
    if (text.isEmpty) return '请输入密码';
    if (_register && (text.length < 8 || text.length > 64)) {
      return '密码需为 8-64 位';
    }
    return null;
  }

  String? _checkInvite(String? value) {
    if ((value ?? '').trim().isEmpty) return '请输入邀请码';
    return null;
  }

  void _toggle() {
    if (_busy) return;
    setState(() {
      _register = !_register;
      _error = null;
    });
  }

  void _cancel() {
    Navigator.of(context).pop();
  }

  Future<void> _submit() async {
    if (_busy) return;
    if (_formKey.currentState?.validate() != true) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    final api = widget.api ?? BrandingApi();
    final username = _username.text.trim();
    final password = _password.text;
    try {
      final BrandingAccount account;
      if (_register) {
        account = await api.register(username, password, _invite.text.trim());
      } else {
        account = await api.login(username, password);
      }
      if (!mounted) return;
      Navigator.of(context).pop(account);
    } on BrandingApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final appLocalizations = context.appLocalizations;
    final accountLabel = appLocalizations.account;
    final passwordLabel = appLocalizations.password;
    final errorStyle = TextStyle(color: context.colorScheme.error);
    final title = _register ? '注册' : '登录';
    final switchText = _register ? '已有账号？去登录' : '没有账号？用邀请码注册';
    final error = _error;
    return CommonDialog(
      title: title,
      actions: [
        TextButton(
          onPressed: _busy ? null : _cancel,
          child: Text(appLocalizations.cancel),
        ),
        TextButton(
          onPressed: _busy ? null : _submit,
          child: Text(appLocalizations.submit),
        ),
      ],
      child: Form(
        key: _formKey,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          spacing: 16,
          children: [
            TextFormField(
              controller: _username,
              enabled: !_busy,
              autocorrect: false,
              enableSuggestions: false,
              textInputAction: TextInputAction.next,
              decoration: InputDecoration(labelText: accountLabel),
              validator: _checkName,
            ),
            TextFormField(
              controller: _password,
              enabled: !_busy,
              obscureText: true,
              textInputAction: TextInputAction.next,
              decoration: InputDecoration(labelText: passwordLabel),
              validator: _checkPassword,
            ),
            if (_register)
              TextFormField(
                controller: _invite,
                enabled: !_busy,
                autocorrect: false,
                enableSuggestions: false,
                textCapitalization: TextCapitalization.characters,
                decoration: const InputDecoration(labelText: '邀请码'),
                validator: _checkInvite,
              ),
            if (error != null) Text(error, style: errorStyle),
            if (_busy) const LinearProgressIndicator(),
            TextButton(
              onPressed: _busy ? null : _toggle,
              child: Text(switchText),
            ),
          ],
        ),
      ),
    );
  }
}
