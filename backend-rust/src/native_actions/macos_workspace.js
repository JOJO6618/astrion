ObjC.import('AppKit');

function string(value) {
    return value.isNil() ? '' : String(ObjC.unwrap(value));
}

function applications(path) {
    const workspace = $.NSWorkspace.sharedWorkspace;
    const document = $.NSURL.fileURLWithPath(path);
    const defaultUrl = workspace.URLForApplicationToOpenURL(document);
    const defaultPath = defaultUrl.isNil() ? '' : string(defaultUrl.path);
    const urls = workspace.URLsForApplicationsToOpenURL(document);
    const result = [];
    for (let index = 0; index < Number(urls.count); index++) {
        const url = urls.objectAtIndex(index);
        const appPath = string(url.path);
        const bundle = $.NSBundle.bundleWithURL(url);
        let label = '';
        let identifier = '';
        if (!bundle.isNil()) {
            label = string(bundle.objectForInfoDictionaryKey('CFBundleDisplayName')) ||
                string(bundle.objectForInfoDictionaryKey('CFBundleName'));
            identifier = string(bundle.bundleIdentifier);
        }
        result.push({ path: appPath, label, bundle_id: identifier, is_default: appPath === defaultPath });
    }
    return JSON.stringify(result);
}

function icon(path) {
    const image = $.NSWorkspace.sharedWorkspace.iconForFile(path);
    if (image.isNil()) throw Error('App icon unavailable');
    const bitmap = $.NSBitmapImageRep.imageRepWithData(image.TIFFRepresentation);
    if (bitmap.isNil()) throw Error('App icon unavailable');
    const png = bitmap.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $.NSDictionary.dictionary);
    if (png.isNil()) throw Error('App icon unavailable');
    return string(png.base64EncodedStringWithOptions(0));
}

function run(argv) {
    if (argv[0] === 'apps') return applications(argv[1]);
    if (argv[0] === 'icon') return icon(argv[1]);
    throw Error('Unknown workspace action');
}
