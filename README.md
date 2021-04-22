# Faiss for CPU benchmark in Deepnote with Docker image

## Description:
Hi! Here you have a docker image to use in Deepnote with Faiss CPU. It builds the image and let you import It into the notebook without further ado.

There's a solved issue that causes a dramatic slow down (It makes it _10x slower than KMeans_ when It's blazing fast even with CPU only). I haven't had this problem in Google Colab nor AWS's Sagemaker but I believe this could arise in other environments.
You can check the issue here: https://github.com/facebookresearch/faiss/issues/53 and you'll see that all It takes to fix It is run `!OMP_WAIT_POLICY=PASSIVE`



### TL;DR
Docker image for Deepnote + fix for slow down + example with 1 million blobs



## Details
 1. Dockerfile to conda install Faiss 1.6.3 with Python 3.7.4 in Deepnote, just copy the code, build and restart your machine.
 2. Deepnote/Jupyter Notebook with a simple but illustrative example to benchmark Sklearn's KMeans and Facebook's Faiss. It also has a class definition to make It more easy to read and compare based on https://www.kdnuggets.com/2021/01/k-means-faster-lower-error-scikit-learn.html
 
 
 ## Results and general impressions:
 
 1. It's truly faster running in the same hardware which is impressive, SKlearn's KMeans has reigned for long years and It's kinda cool see a newcomber. I'll be testing GPU soon!
 2. The installation could be painfull, I've spent more hours trying to run It in different setups than I can publicly say. It's different for Google Collaboratory, Sagemaker Notebook's instances and Deepnote.
 3. Without the referenced closed issue I wouldn't be able to run It properly, kudos to the community!
 
When the documentation and easyness of installing improves It'll be a no brainer option for every entry/middle level data scientist, in the midtime I believe that'll be more a niche thing just because It requires dedication.
 
 
