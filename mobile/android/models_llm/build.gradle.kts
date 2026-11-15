// Fast-follow Play Asset Delivery pack: Play downloads it right after install
// (the app has no INTERNET) and the app reads the files where Play put them. CI copies the checked
// model files into src/main/assets (tool/fill_asset_packs.py); the folder is empty in git.
plugins {
    id("com.android.asset-pack")
}

assetPack {
    packName.set("models_llm")
    dynamicDelivery {
        deliveryType.set("fast-follow")
    }
}
