# WorkManager (pulled in by com.google.android.play:asset-delivery) opens its Room database from
# androidx.startup at launch. Room creates the generated *_Impl class by reflection, and R8 full mode
# (AGP 9 default) dropped WorkDatabase_Impl's no-arg constructor, so the release build crashed on
# launch with "Failed to create an instance of class androidx.work.impl.WorkDatabase" (#1023).
-keep class * extends androidx.room.RoomDatabase { <init>(); }
-keep class androidx.work.impl.WorkDatabase_Impl { *; }
