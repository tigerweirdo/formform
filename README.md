FORM/FORM durum dalı. Bu dalı elle düzenlemeyin, sistem yazar.

- videos.csv      Instagram'da yayınlanan videolar ve ölçümleri (sistemin öğrendiği her şeyin kaynağı)
- dna_weights.csv Instagram'dan öğrenilen ağırlıklar (hangi yapı/palet/hareket iyi çalışıyor)
- youtube/, tiktok/ aynı iki dosya, her platform için ayrı (platformların verisi birbirine karışmaz)
- config.json     jeton yenileme tarihleri (jetonun kendisi değil)
- ig_token.sealed şifreli Instagram jetonu (anahtarı GitHub gizli değeri olan IG_ACCESS_TOKEN)
- tiktok_token.sealed şifreli TikTok jetonları (anahtarı GitHub gizli değeri olan TIKTOK_CLIENT_SECRET)

Kod `main` dalındadır.
