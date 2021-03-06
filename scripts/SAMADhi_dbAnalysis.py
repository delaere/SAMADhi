#!/usr/bin/env python

# Script to do basic checks to the database and output statistics on usage and issues

import os,errno,json
import ROOT
ROOT.gROOT.SetBatch()
from optparse import OptionParser, OptionGroup
from datetime import date
from cp3_llbb.SAMADhi.SAMADhi import Dataset, Sample, Result, MadWeight, DbStore
from storm.info import get_cls_info
from datetime import datetime
from collections import defaultdict
from das_import import get_data

class MyOptionParser:
    """
    Client option parser
    """
    def __init__(self):
        usage  = "Usage: %prog [options]\n"
        self.parser = OptionParser(usage=usage)
        self.parser.add_option("-p","--path", action="store", type="string",
                               dest="path", default=datetime.now().strftime("%y%m%d-%H:%M:%S"),
             help="Destination path")
        self.parser.add_option("-b","--basedir", action="store", type="string",
                               dest="basedir", default="",
             help="Directory where the website will be installed")
        self.parser.add_option("-f","--full", action="store_true",
                               dest="DAScrosscheck", default=False,
             help="Full check: compares each Dataset entry to DAS and check for consistency (slow!)")
        self.parser.add_option("-d","--dry", action="store_true",
                               dest="dryRun", default=False,
             help="Dry run: do no write to disk")
        # ---- DAS options 
        das_group = OptionGroup(self.parser,"DAS options",
                                "The following options control the communication with the DAS server")
        msg  = "host name of DAS cache server, default is https://cmsweb.cern.ch"
        das_group.add_option("--host", action="store", type="string",
                       default='https://cmsweb.cern.ch', dest="host", help=msg)
        msg  = "index for returned result"
        das_group.add_option("--idx", action="store", type="int",
                               default=0, dest="idx", help=msg)
        msg  = 'query waiting threshold in sec, default is 5 minutes'
        das_group.add_option("--threshold", action="store", type="int",
                               default=300, dest="threshold", help=msg)
        msg  = 'specify private key file name'
        das_group.add_option("--key", action="store", type="string",
                               default="", dest="ckey", help=msg)
        msg  = 'specify private certificate file name'
        das_group.add_option("--cert", action="store", type="string",
                               default="", dest="cert", help=msg)
        msg = 'specify number of retries upon busy DAS server message'
        das_group.add_option("--retry", action="store", type="string",
                               default=0, dest="retry", help=msg)
        msg = 'drop DAS headers'
        das_group.add_option("--das-headers", action="store_true",
                               default=False, dest="das_headers", help=msg)
        msg = 'verbose output'
        das_group.add_option("-v", "--verbose", action="store",
                               type="int", default=0, dest="verbose", help=msg)
        self.parser.add_option_group(das_group)

    def get_opt(self):
        """
        Returns parse list of options
        """
        opts, args = self.parser.parse_args()
        if opts.path is not None:
          opts.path = os.path.abspath(os.path.expandvars(os.path.expanduser(opts.path)))
        if not opts.dryRun and os.path.exists(opts.path):
           raise OSError(errno.EEXIST,"Existing directory",opts.path);
        return opts

def main():
    """Main function"""
    # get the options
    optmgr = MyOptionParser()
    opts = optmgr.get_opt()
    # connect to the MySQL database using default credentials
    dbstore = DbStore()
    # prepare the output directory
    if not os.path.exists(opts.path) and not opts.dryRun:
      os.makedirs(opts.path)
    # run each of the checks and collect data

    # collect general statistics
    outputDict = collectGeneralStats(dbstore,opts)
    if not opts.dryRun:
      with open(opts.path+'/stats.json', 'w') as outfile:
        json.dump(outputDict, outfile, default=encode_storm_object)
	force_symlink(opts.path+'/stats.json',opts.basedir+'/data/stats.json')
 
    # check datasets
    outputDict = {}
    outputDict["DatabaseInconsistencies"] = checkDatasets(dbstore,opts) if opts.DAScrosscheck else []
    outputDict["Orphans"] = findOrphanDatasets(dbstore,opts)
    outputDict["IncompleteData"] = checkDatasetsIntegrity(dbstore,opts)
    outputDict["DatasetsStatistics"] = analyzeDatasetsStatistics(dbstore,opts)
    if not opts.dryRun:
      with open(opts.path+'/DatasetsAnalysisReport.json', 'w') as outfile:
        json.dump(outputDict, outfile, default=encode_storm_object)
	force_symlink(opts.path+'/DatasetsAnalysisReport.json',opts.basedir+'/data/DatasetsAnalysisReport.json')

    # check samples
    outputDict = {}
    outputDict["MissingDirSamples"] = checkSamplePath(dbstore,opts)
    outputDict["DatabaseInconsistencies"] = checkSampleConsistency(dbstore,opts)
    outputDict["SampleStatistics"] = analyzeSampleStatistics(dbstore,opts)
    if not opts.dryRun:
      with open(opts.path+'/SamplesAnalysisReport.json', 'w') as outfile:
        json.dump(outputDict, outfile, default=encode_storm_object)
	force_symlink(opts.path+'/SamplesAnalysisReport.json',opts.basedir+'/data/SamplesAnalysisReport.json')

    # now, check results
    outputDict = {}
    outputDict["MissingDirSamples"] = checkResultPath(dbstore,opts)
    outputDict["DatabaseInconsistencies"] = checkResultConsistency(dbstore,opts)
    outputDict["SelectedResults"] = selectResults(dbstore,opts)
    outputDict["ResultsStatistics"] = analyzeResultsStatistics(dbstore,opts)
    if not opts.dryRun:
      with open(opts.path+'/ResultsAnalysisReport.json', 'w') as outfile:
        json.dump(outputDict, outfile, default=encode_storm_object)
	force_symlink(opts.path+'/ResultsAnalysisReport.json',opts.basedir+'/data/ResultsAnalysisReport.json')

def collectGeneralStats(dbstore,opts):
    # get number of datasets, samples, results, analyses
    result = {}
    results = dbstore.find(Result)
    samples = dbstore.find(Sample)
    datasets = dbstore.find(Dataset)
    result["nDatasets"] = datasets.count()
    result["nSamples"] = samples.count()
    result["nResults"] = results.count()
    result["nAnalyses"] = 0
    print "\nGeneral statistics:"
    print '======================'
    print datasets.count(), " datasets"
    print samples.count(), " samples"
    print results.count(), " results"
    return result

def checkDatasets(dbstore,opts):
    datasets = dbstore.find(Dataset)
    print "\nDatasets inconsistent with DAS:"
    print '=================================='
    result = []
    for dataset in datasets:
      query1 = "dataset="+dataset.name+" | grep dataset.name, dataset.nevents, dataset.size, dataset.tag, dataset.datatype, dataset.creation_time"
      query2 = "release dataset="+dataset.name+" | grep release.name"
      query3 = "config dataset="+dataset.name+" | grep config.global_tag,config.name=cmsRun"
      das_response1 = get_data(opts.host, query1, opts.idx, 1, opts.verbose, opts.threshold, opts.ckey, opts.cert, opts.das_headers)
      das_response2 = get_data(opts.host, query2, opts.idx, 1, opts.verbose, opts.threshold, opts.ckey, opts.cert, opts.das_headers)
      das_response3 = get_data(opts.host, query3, opts.idx, 1, opts.verbose, opts.threshold, opts.ckey, opts.cert, opts.das_headers)
      tmp = [{u'dataset' : [{}]},]
      for i in range(0,len(das_response1[0]["dataset"])):
          if das_response1[0]["dataset"][i]["name"]==dataset.name:
              for key in das_response1[0]["dataset"][i]:
                  tmp[0]["dataset"][0][key] = das_response1[0]["dataset"][i][key]
      if not "tag" in tmp[0]["dataset"][0]:
          tmp[0]["dataset"][0][u'tag']=None
      das_response1 = tmp
      try:
         test1 = das_response2[0]["release"][0]["name"]=="unknown" or dataset.cmssw_release == das_response2[0]["release"][0]["name"], 
         test2 = dataset.datatype == das_response1[0]["dataset"][0]["datatype"],
         test3 = dataset.nevents == das_response1[0]["dataset"][0]["nevents"], 
         test4 = dataset.dsize == das_response1[0]["dataset"][0]["size"]
      except:
         result.append([dataset,"Inconsistent with DAS"])
         print "%s (imported on %s)"%(str(dataset.name),str(dataset.creation_time))
      else:
         if not(test1 and test2 and test3 and test4):
             result.append([dataset,"Inconsistent with DAS"])
             print "%s (imported on %s)"%(str(dataset.name),str(dataset.creation_time))
    return result

def findOrphanDatasets(dbstore,opts):
    datasets = dbstore.find(Dataset)
    print "\nOrphan Datasets:"
    print '==================='
    result = []
    for dataset in datasets:
        if dataset.samples.count()==0:
            result.append(dataset)
            print "%s (imported on %s)"%(str(dataset.name),str(dataset.creation_time))
    if len(result)==0:
       print "None"
    return result

def checkDatasetsIntegrity(dbstore,opts):
    datasets = dbstore.find(Dataset)
    print "\nDatasets integrity issues:"
    print '==========================='
    result = []
    for dataset in datasets:
        if dataset.cmssw_release is None:
            result.append([dataset,"missing CMSSW release"])
            print "%s (imported on %s): missing CMSSW release"%(str(dataset.name),str(dataset.creation_time))
        elif dataset.energy is None:
            result.append([dataset,"missing Energy"])
            print "%s (imported on %s): missing Energy"%(str(dataset.name),str(dataset.creation_time))
        elif dataset.globaltag is None:
            result.append([dataset,"missing Globaltag"])
            print "%s (imported on %s): missing Globaltag"%(str(dataset.name),str(dataset.creation_time))
    if len(result)==0:
       print "None"
    return result
    

def analyzeDatasetsStatistics(dbstore,opts):
    # ROOT output
    if not opts.dryRun:
      rootfile = ROOT.TFile(opts.path+"/analysisReport.root","update")
    stats = {}
    # Releases used
    output =  dbstore.execute("select dataset.cmssw_release,COUNT(dataset.dataset_id) as numOfDataset FROM dataset GROUP BY cmssw_release")
    stats["cmssw_release"] = output.get_all()
    releasePie = ROOT.TPie("datasetReleasePie","Datasets release",len(stats["cmssw_release"]))
    for index,entry in enumerate(stats["cmssw_release"]):
      releasePie.SetEntryVal(index,entry[1])
      releasePie.SetEntryLabel(index,"None" if entry[0] is None else entry[0])
    releasePie.SetTextAngle(0);
    releasePie.SetRadius(0.3);
    releasePie.SetTextColor(1);
    releasePie.SetTextFont(62);
    releasePie.SetTextSize(0.03);
    canvas = ROOT.TCanvas("datasetRelease","",2)
    releasePie.Draw("r")
    if not opts.dryRun:
      ROOT.gPad.Write()
    # GlobalTag used
    output =  dbstore.execute("select dataset.globaltag,COUNT(dataset.dataset_id) as numOfDataset FROM dataset GROUP BY globaltag")
    stats["globaltag"] = output.get_all()
    globaltagPie = ROOT.TPie("datasetGTPie","Datasets globaltag",len(stats["globaltag"]))
    for index,entry in enumerate(stats["globaltag"]):
      globaltagPie.SetEntryVal(index,entry[1])
      globaltagPie.SetEntryLabel(index,"None" if entry[0] is None else entry[0])
    globaltagPie.SetTextAngle(0);
    globaltagPie.SetRadius(0.3);
    globaltagPie.SetTextColor(1);
    globaltagPie.SetTextFont(62);
    globaltagPie.SetTextSize(0.03);
    canvas = ROOT.TCanvas("datasetGT","",2)
    globaltagPie.Draw("r")
    if not opts.dryRun:
      ROOT.gPad.Write()
    # Datatype
    output =  dbstore.execute("select dataset.datatype,COUNT(dataset.dataset_id) as numOfDataset FROM dataset GROUP BY datatype")
    stats["datatype"] = output.get_all()
    datatypePie = ROOT.TPie("datasetTypePie","Datasets datatype",len(stats["datatype"]))
    for index,entry in enumerate(stats["datatype"]):
      datatypePie.SetEntryVal(index,entry[1])
      datatypePie.SetEntryLabel(index,"None" if entry[0] is None else entry[0])
    datatypePie.SetTextAngle(0);
    datatypePie.SetRadius(0.3);
    datatypePie.SetTextColor(1);
    datatypePie.SetTextFont(62);
    datatypePie.SetTextSize(0.03);
    canvas = ROOT.TCanvas("datasetType","",2)
    datatypePie.Draw("r")
    if not opts.dryRun:
      ROOT.gPad.Write()
    # Energy
    output =  dbstore.execute("select dataset.energy,COUNT(dataset.dataset_id) as numOfDataset FROM dataset GROUP BY energy")
    stats["energy"] = output.get_all()
    energyPie = ROOT.TPie("datasetEnergyPie","Datasets energy",len(stats["energy"]))
    for index,entry in enumerate(stats["energy"]):
      energyPie.SetEntryVal(index,entry[1])
      energyPie.SetEntryLabel(index,"None" if entry[0] is None else str(entry[0]))
    energyPie.SetTextAngle(0);
    energyPie.SetRadius(0.3);
    energyPie.SetTextColor(1);
    energyPie.SetTextFont(62);
    energyPie.SetTextSize(0.03);
    canvas = ROOT.TCanvas("datasetEnergy","",2)
    energyPie.Draw("r")
    if not opts.dryRun:
      ROOT.gPad.Write()
    # get all datasets to loop
    datasets = dbstore.find(Dataset)
    datasets.order_by(Dataset.creation_time)
    # time evolution of # datasets (still in db)
    datasets_time = [[0,0]]
    # various stats (histograms)
    datasets_nsamples = ROOT.TH1I("dataseets_nsamples","datasets_nsamples",10,0,10)
    datasets_nevents  = ROOT.TH1I("dataseets_nevents", "datasets_nevents" ,100,0,-100)
    datasets_dsize    = ROOT.TH1I("dataseets_dsize",   "datasets_dsize"   ,100,0,-100)
    # let's go... loop
    for dataset in datasets:
        # for Highcharts the time format is #seconds since epoch
        time = int(dataset.creation_time.strftime("%s"))*1000
        datasets_time.append([time,datasets_time[-1][1]+1])
        datasets_nsamples.Fill(dataset.samples.count())
        datasets_nevents.Fill(dataset.nevents)
        datasets_dsize.Fill(dataset.dsize)
    # drop this: just to initialize the loop
    datasets_time.pop(0)
    # output
    stats["datasetsTimeprof"] = datasets_time
    datasetsTimeprof_graph = ROOT.TGraph(len(datasets_time))
    for i,s in enumerate(datasets_time):
        datasetsTimeprof_graph.SetPoint(i,s[0]/1000,s[1])
    if not opts.dryRun:
        datasetsTimeprof_graph.Write("datasetsTimeprof_graph")
    data = []
    for bin in range(1,datasets_nsamples.GetNbinsX()+1):
        data.append([datasets_nsamples.GetBinCenter(bin),datasets_nsamples.GetBinContent(bin)])
    stats["datasetsNsamples"] = data
    data = []
    for bin in range(1,datasets_nevents.GetNbinsX()+1):
        data.append([datasets_nevents.GetBinCenter(bin),datasets_nevents.GetBinContent(bin)])
    stats["datasetsNevents"] = data
    data = []
    for bin in range(1,datasets_dsize.GetNbinsX()+1):
        data.append([datasets_dsize.GetBinCenter(bin),datasets_dsize.GetBinContent(bin)])
    stats["datasetsDsize"] = data
    # some printout
    print "\nDatasets Statistics extracted."
    print '================================='
    # ROOT output
    if not opts.dryRun:
      rootfile.Write();
      rootfile.Close();
    # JSON output
    return stats


def checkResultPath(dbstore,opts):
    # get all samples
    result = dbstore.find(Result)
    print "\nResults with missing path:"
    print '==========================='
    array = []
    for res in result:
      # check that the path exists, and keep track of the sample if not the case.
      if not os.path.exists(res.path):
        print "Result #%s (created on %s by %s):"%(str(res.result_id),str(res.creation_time),str(res.author)),
        print " missing path: %s" %res.path
        array.append(res)
    if len(array)==0: print "None"
    return array

    
def checkSamplePath(dbstore,opts):
    # get all samples
    result = dbstore.find(Sample)
    print "\nSamples with missing path:"
    print '==========================='
    array = []
    for sample in result:
      # check that the path exists, and keep track of the sample if not the case.
      if not os.path.exists(sample.path):
        print "Sample #%s (created on %s by %s):"%(str(sample.sample_id),str(sample.creation_time),str(sample.author)),
        print " missing path: %s" %sample.path
        array.append(sample)
    if len(array)==0: print "None"
    return array


def selectResults(dbstore,opts):
    # look for result records pointing to a ROOT file
    # eventually further filter 
    results = dbstore.find(Result)
    print "\nSelected results:"
    print '==========================='
    array = []
    for result in results:
        path = result.path
        if os.path.exists(path) and os.path.isdir(path):
            files = [ f for f in os.listdir(path) if os.path.isfile(path+"/"+f) ]
            if len(files)==1:
                path = path+"/"+f
		result.path = path
	if os.path.exists(path) and os.path.isfile(path) and path.lower().endswith(".root"):
	    symlink = "%s/data/result_%s.root"%(opts.basedir,str(result.result_id))
	    relpath = "../data/result_%s.root"%(str(result.result_id))
	    force_symlink(path,symlink)
	    array.append([result,relpath])
            print "Result #%s (created on %s by %s): "%(str(result.result_id),str(result.creation_time),str(result.author)),
            print symlink

    if len(array)==0: print "None"
    return array

def checkResultConsistency(dbstore,opts):
    # get all samples
    result = dbstore.find(Result)
    print "\nResults with missing source:"
    print '============================='
    array = []
    for res in result:
      # check that the source sample exists in the database.
      # normaly, this should be protected already at the level of sql rules
      for sample in res.samples:
        if sample is None:
          print "Result #%s (created on %s by %s):"%(str(res.result_id),str(res.creation_time),str(res.author)),
          print "inconsistent source sample"
          array.append([res,"inconsistent source sample"])
          print res
          break
    if len(array)==0: print "None"
    return array


def checkSampleConsistency(dbstore,opts):
    # get all samples
    result = dbstore.find(Sample)
    print "\nSamples with missing source:"
    print '============================='
    array = []
    for sample in result:
      # check that either the source dataset or the source sample exists in the database.
      # normaly, this should be protected already at the level of sql rules
      sourceDataset = sample.source_dataset
      sourceSample = sample.source_sample
      if (sample.source_dataset_id is not None) and (sourceDataset is None):
        print "Sample #%s (created on %s by %s):"%(str(sample.sample_id),str(sample.creation_time),str(sample.author)),
        print "inconsistent source dataset"
        array.append([sample,"inconsistent source dataset"])
        print sample
      if (sample.source_sample_id is not None) and (sourceSample is None):
        print "Sample #%s (created on %s by %s):"%(str(sample.sample_id),str(sample.creation_time),str(sample.author)),
        print "inconsistent source sample"
        array.append([sample,"inconsistent source sample"])
    if len(array)==0: print "None"
    return array


def analyzeResultsStatistics(dbstore,opts):
    stats = {}
    # ROOT output
    if not opts.dryRun:
      rootfile = ROOT.TFile(opts.path+"/analysisReport.root","update")
    #authors statistics
    output =  dbstore.execute("select result.author,COUNT(result.result_id) as numOfResults FROM result GROUP BY author")
    stats["resultsAuthors"] = output.get_all()
    authorPie = ROOT.TPie("resultsAuthorsPie","Results authors",len(stats["resultsAuthors"]))
    for index,entry in enumerate(stats["resultsAuthors"]):
      authorPie.SetEntryVal(index,entry[1])
      authorPie.SetEntryLabel(index,"None" if entry[0] is None else entry[0])
    authorPie.SetTextAngle(0);
    authorPie.SetRadius(0.3);
    authorPie.SetTextColor(1);
    authorPie.SetTextFont(62);
    authorPie.SetTextSize(0.03);
    canvas = ROOT.TCanvas("resultsAuthor","",2)
    authorPie.Draw("r")
    if not opts.dryRun:
      ROOT.gPad.Write()
    result_nsamples = ROOT.TH1I("result_nsamples","result_nsamples",20,0,20)
    # get all samples to loop
    results = dbstore.find(Result)
    results.order_by(Result.creation_time)
    # time evolution of # results (still in db)
    results_time = [[0,0]]
    # let's go... loop
    for result in results:
        # for Highcharts the time format is #seconds since epoch
        time = int(result.creation_time.strftime("%s"))*1000
        results_time.append([time,results_time[-1][1]+1])
        result_nsamples.Fill(result.samples.count())
    # drop this: just to initialize the loop
    results_time.pop(0)
    # output
    stats["resultsTimeprof"] = results_time
    resultsTimeprof_graph = ROOT.TGraph(len(results_time))
    for i,s in enumerate(results_time):
        resultsTimeprof_graph.SetPoint(i,s[0]/1000,s[1])
    if not opts.dryRun:
        resultsTimeprof_graph.Write("resultsTimeprof_graph")
    data = []
    for bin in range(1,result_nsamples.GetNbinsX()+1):
        data.append([result_nsamples.GetBinCenter(bin),result_nsamples.GetBinContent(bin)])
    stats["resultNsamples"] = data
    # some printout
    print "\nResults Statistics extracted."
    print '================================'
    # ROOT output
    if not opts.dryRun:
      rootfile.Write();
      rootfile.Close();
    # JSON output
    return stats

def analyzeSampleStatistics(dbstore,opts):
    stats = {}
    # ROOT output
    if not opts.dryRun:
      rootfile = ROOT.TFile(opts.path+"/analysisReport.root","update")
    #authors statistics
    output =  dbstore.execute("select sample.author,COUNT(sample.sample_id) as numOfSamples FROM sample GROUP BY author")
    stats["sampleAuthors"] = output.get_all()
    authorPie = ROOT.TPie("sampleAuthorsPie","Samples authors",len(stats["sampleAuthors"]))
    for index,entry in enumerate(stats["sampleAuthors"]):
      authorPie.SetEntryVal(index,entry[1])
      authorPie.SetEntryLabel(index,"None" if entry[0] is None else entry[0])
    authorPie.SetTextAngle(0);
    authorPie.SetRadius(0.3);
    authorPie.SetTextColor(1);
    authorPie.SetTextFont(62);
    authorPie.SetTextSize(0.03);
    canvas = ROOT.TCanvas("sampleAuthor","",2)
    authorPie.Draw("r")
    if not opts.dryRun:
      ROOT.gPad.Write()
    #sample types statistics
    output =  dbstore.execute("select sample.sampletype,COUNT(sample.sample_id) as numOfSamples FROM sample GROUP BY sampletype")
    stats["sampleTypes"] = output.get_all()
    typePie = ROOT.TPie("sampleTypesPie","Samples types",len(stats["sampleTypes"]))
    for index,entry in enumerate(stats["sampleTypes"]):
      typePie.SetEntryVal(index,entry[1])
      typePie.SetEntryLabel(index,"None" if entry[0] is None else entry[0])
    typePie.SetTextAngle(0);
    typePie.SetRadius(0.3);
    typePie.SetTextColor(1);
    typePie.SetTextFont(62);
    typePie.SetTextSize(0.03);
    canvas = ROOT.TCanvas("sampleType","",2)
    typePie.Draw("r")
    if not opts.dryRun:
      ROOT.gPad.Write()
    # get all samples to loop
    result = dbstore.find(Sample)
    result.order_by(Sample.creation_time)
    # events statistics
    sample_nevents_processed = ROOT.TH1I("sample_nevents_processed","sample_nevents_processed",100,0,-100)
    sample_nevents = ROOT.TH1I("sample_nevents","sample_nevents",100,0,-100)
    # time evolution of statistics & # samples (still in db)
    sample_nevents_processed_time = [[0,0]]
    sample_nevents_time = [[0,0]] 
    samples_time = [[0,0]]
    # let's go... loop
    for sample in result:
        # for Highcharts the time format is #seconds since epoch
        time = int(sample.creation_time.strftime("%s"))*1000
        ne = 0 if sample.nevents is None else sample.nevents
        np = 0 if sample.nevents_processed is None else sample.nevents_processed
        sample_nevents_processed.Fill(np)
        sample_nevents.Fill(ne)
        sample_nevents_processed_time.append([time,sample_nevents_processed_time[-1][1]+np])
        sample_nevents_time.append([time,sample_nevents_time[-1][1]+ne])
        samples_time.append([time,samples_time[-1][1]+1])
    # drop this: just to initialize the loop
    sample_nevents_processed_time.pop(0)
    sample_nevents_time.pop(0)
    samples_time.pop(0)
    # output
    stats["sampleNeventsTimeprof"] = sample_nevents_time
    stats["sampleNeventsProcessedTimeprof"] = sample_nevents_processed_time
    stats["samplesTimeprof"] = samples_time
    sampleNeventsTimeprof_graph = ROOT.TGraph(len(sample_nevents_time))
    sampleNeventsProcessedTimeprof_graph = ROOT.TGraph(len(sample_nevents_processed_time))
    samplesTimeprof_graph = ROOT.TGraph(len(samples_time))
    for i,s in enumerate(sample_nevents_time):
        sampleNeventsTimeprof_graph.SetPoint(i,s[0]/1000,s[1])
    for i,s in enumerate(sample_nevents_processed_time):
        sampleNeventsProcessedTimeprof_graph.SetPoint(i,s[0]/1000,s[1])
    for i,s in enumerate(samples_time):
        samplesTimeprof_graph.SetPoint(i,s[0]/1000,s[1])
    if not opts.dryRun:
        sampleNeventsTimeprof_graph.Write("sampleNeventsTimeprof_graph")
        sampleNeventsProcessedTimeprof_graph.Write("sampleNeventsProcessedTimeprof_graph")
        samplesTimeprof_graph.Write("samplesTimeprof_graph")
    # unfortunately, TBufferJSON is not available in CMSSW (no libRHttp) -> no easy way to export to JSON
    # the JSON format for highcharts data is [ [x1,y1], [x2,y2], ... ]
    data = []
    for bin in range(1,sample_nevents.GetNbinsX()+1):
      data.append([sample_nevents.GetBinCenter(bin),sample_nevents.GetBinContent(bin)])
    stats["sampleNevents"] = data
    data = []
    for bin in range(1,sample_nevents_processed.GetNbinsX()+1):
      data.append([sample_nevents_processed.GetBinCenter(bin),sample_nevents_processed.GetBinContent(bin)])
    stats["sampleNeventsProcessed"] = data
    # some printout
    print "\nSamples Statistics extracted."
    print '================================'
    # ROOT output
    if not opts.dryRun:
      rootfile.Write();
      rootfile.Close();
    # JSON output
    return stats

# function to serialize the storm objects,
# from Jamu Kakar and Mario Zito at https://lists.ubuntu.com/archives/storm/2010-May/001286.html
def encode_storm_object(object):
    ''' Serializes to JSON a Storm object
    
    Use:
        from storm.info import get_cls_info
        import json
        ...
        storm_object = get_storm_object()
        print json.dumps(storm_object, default=encode_storm_object)
            
    Warnings:
        Serializes objects containing Int, Date and Unicode data types
        other datatypes are not tested. MUST be improved
    '''
    if not hasattr(object, "__storm_table__"):
        raise TypeError(repr(object) + " is not JSON serializable")
    result = {}
    cls_info = get_cls_info(object.__class__)
    for name in cls_info.attributes.iterkeys():
        value= getattr(object, name)
        if (isinstance(value, date)): 
            value= str(value)
        result[name] = value
    return result

def force_symlink(file1, file2):
    try:
        os.symlink(file1, file2)
    except OSError, e:
        if e.errno == errno.EEXIST:
            os.remove(file2)
            os.symlink(file1, file2)

#
# main
#
if __name__ == '__main__':
    main()

