# Samsung ExifTool fixtures

ExifTool output (`-json -G1 -n -ee3 -api LargeFileSupport=1`) of three real Samsung Galaxy videos
(two Android 12 phones, one Galaxy S26 Ultra on Android 16), kept as regression data for the
capture-time and device rules. File-system tags other than `FileModifyDate` were
removed and the GPS position was replaced by the Eiffel Tower (same time zone as the original).

`../gopro_gpmf_paris.mp4` (3.6 KB) is a synthetic clip with a real `gpmd` GoPro metadata track
(GPS5 + GPSU + SCAL, two samples at the Eiffel Tower) used to check embedded-track extraction.
