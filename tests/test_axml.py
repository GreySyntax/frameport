from frameport.apk import axml


def test_parse(quest_manifest):
    x = axml.Axml(quest_manifest)
    names = [e.name for e in x.elements()]
    assert names[:3] == ["manifest", "uses-permission", "uses-permission"]
    assert axml.categories(quest_manifest) == {axml.INFO}
    assert x.get_bool("application", "debuggable") is True


def test_fix_launcher(quest_manifest):
    fixed = axml.fix_launcher(quest_manifest)
    assert fixed and axml.categories(fixed) == {axml.LAUNCHER}
    # idempotent: a manifest that already has LAUNCHER is left alone
    assert axml.fix_launcher(fixed) is None
    # everything outside the string pool and the retargeted attribute is untouched
    assert len(fixed) > len(quest_manifest)


def test_nodebug(quest_manifest):
    fixed = axml.set_bool_attr(quest_manifest, "application", "debuggable", False)
    assert axml.Axml(fixed).get_bool("application", "debuggable") is False
    assert len(fixed) == len(quest_manifest)


def test_meta_permissions(quest_manifest):
    assert axml.undeclared_meta_permissions(quest_manifest) == ["com.oculus.permission.USE_SCENE"]
    fixed, added = axml.define_meta_permissions(quest_manifest)
    assert added == ["com.oculus.permission.USE_SCENE"]
    used, declared = axml.used_and_declared_permissions(fixed)
    assert "com.oculus.permission.USE_SCENE" in declared
    assert axml.define_meta_permissions(fixed) is None


def test_edits_compose(quest_manifest):
    m = axml.fix_launcher(quest_manifest)
    m = axml.set_bool_attr(m, "application", "debuggable", False)
    m, _ = axml.define_meta_permissions(m)
    assert axml.categories(m) == {axml.LAUNCHER}
    assert axml.Axml(m).get_bool("application", "debuggable") is False
